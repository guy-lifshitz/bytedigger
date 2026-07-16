# Shrinking the Human in the Loop

> *A year of figuring out how to make AI check AI code for real, one gate at a time.*

*Guy Lifshitz*

---

Everyone talks about AI writing code. That's not news anymore. The real problem starts one step later: someone has to check that code. The industry's answer is a loop -- generate, review, fix, review again -- with either a human or another model turning the crank. The loop is expensive, it takes lap after lap to converge, and when the reviewer is also a model, it shares the writer's blind spots.

We took a different bet. ByteDigger is built to kill that loop, not to automate it. Checks move to before the code exists: the spec freezes first and gets machine-verified against the real codebase, failing tests land and face a hostile audit before a single line of implementation, and the checks that matter run as cheap deterministic code, not as more LLM judgment. Reviewers still run at the end. The design goal is that they find nothing.

Two principles drive every decision, and they turn out to be the same decision. Quality first: an agent must not be able to game its own acceptance signal, ever. Economics first: every check runs at the cheapest layer that can produce it -- regex, AST, a diff, a byte count -- and a model gets called only when code cannot decide. We do not burn tokens on judgment where a grep gives the same answer. The vendors are happy to sell you a review loop that laps forever; a regex gate costs nothing and never gets tired.

Here's how we got there.

## The bottleneck

I'd ask Claude for a feature, get 400 lines back, and spend an hour reading every one of them. Net time saved: maybe 30 minutes on a good day. So the next thought writes itself: let AI review AI code. Except that's where it gets ugly. AI reviewing AI produces what I call assertion theater. Tests that pass and verify nothing. Reviews that say "looks good" without catching real issues. The writer and the reviewer collude -- no malice required, they just share the same blind spots.

Most agentic coding failures are not capability failures. They are verification failures. That reframing took me far too long, and it changed what we built.

## Specs: necessary, and prose isn't enough

My first approach was spec-driven development. Write detailed specs -- user stories, data models, interfaces -- then generate code that matches them. The theory: constrain the AI enough and the output will be correct.

Specs matter. Without them, AI hallucinates architecture. But prose specs hit two walls. First, you're still the bottleneck, just at a different stage: writing good specs takes about as long as writing code. Second, and worse, AI-generated specs drift from the actual codebase. References to functions that don't exist, quoted signatures that were true three commits ago, interfaces that conflict with what's already there. A spec that cites fiction produces tests that assert fiction, and everything downstream inherits the lie.

So the spec stopped being a document and became an artifact the machine verifies. Two mechanisms:

**Citation verification.** Before a spec freezes, a deterministic lint checks every citation in it against the real repository. Quoted function signatures must match the actual source. Named symbols must resolve to real files. A cited line must exist where the spec says it does. This is a lint, not a model call: exact matching with a windowed search for drift. A spec that references code that isn't there gets rejected at write time, hours before any reviewer would have noticed.

**Acceptance criteria that compile.** Alongside the prose, each spec carries a machine-readable block that maps every acceptance criterion to a mechanical check from a closed registry: file contains this string, command exits zero, and so on. The engine validates the block at freeze time and executes it as code, so "done" means the AC table passes, not that somebody's judgment felt satisfied. A criterion that needs judgment has to declare itself as one, which keeps the escape hatch visible instead of ambient.

Specs stop being documentation that drifts. They compile.

## TDD: right idea, but AI cheats

Tests are binary. Pass or fail. No subjective judgment. So the pipeline is strict TDD.

Quick primer for anyone outside the TDD world: the RED phase writes tests that FAIL, before any implementation exists. The GREEN phase writes only enough code to turn them green. If tests already pass in RED, they aren't testing anything real.

This worked for about a week. Then patterns emerged. `expect(true).toBe(true)` -- assertion theater. Tests that checked mocks instead of behavior. And my personal favorite: "tests aren't needed for this simple change" -- rationalization from an agent that wanted to skip the hard part.

But the real killer was assertion gaming. Kent Beck has talked about this. Even the best models do it: when a test fails in GREEN, the model changes the test assertion to match reality instead of fixing the code. API returns 404? Instead of fixing the endpoint, it updates the test to expect 404. Done -- tests green, feature broken. This is not a prompting failure you can fix with better instructions. Models optimize for "make tests pass," not "make code correct." The only fix is external validation, from outside the agent's context.

One more failure mode deserves its own name: the vacuous RED. An agent writes a test file that imports the unit under test and then mocks that same unit inside the test. The test fails before implementation, passes after, and verifies nothing at all -- it exercises the mock. We got burned by this exactly once. Now a deterministic lint catches it: a symbol that is both imported and patched in the same test file is an automatic reject, no model involved.

## The loop, killed

Here's the shape of the pipeline now:

```
spec (frozen, machine-verified) -> RED (failing tests) -> gate (adversarial audit) -> GREEN (implement) -> verify
            ^                            |
            +--------- REJECT -----------+
```

**Spec freezes first**, with an AC table and a file allowlist. Citation lint and AC validation run before the freeze. Scope drift dies at write time.

**RED lands failing tests.** The engine runs them itself and checks that they fail -- it does not take the agent's word for it. The deterministic red lints run here: stub-passability, fixture checks, collection health.

**The gate audits as an adversary.** A separate, stronger model validates the tests against the spec: every acceptance criterion maps to a test, every test maps back to a criterion, assertions exercise real behavior. The gate cannot write or modify tests. It returns a verdict, and REJECT routes back to the test writer. It rejects a lot. That is the point -- a vacuous test gets killed while killing it is still cheap.

**GREEN implements with the tests read-only.** A diff guard compares the test files before and after implementation and classifies every change. Assertion gaming is a hard fail. A scope lint flags any write outside the spec's file allowlist.

**Verify runs the suite** -- the full one, not the convenient subset. Passing a scoped run while breaking the tree is one of the classic cheats, so the engine treats "which tests ran" as part of the signal.

Notice what is absent: the generate-review-fix loop. There is nothing to babysit, because the pipeline removes the problems the loop exists to find before the code exists. When a build finishes, the review agents at the end are a safety net, not a workflow.

Security is half the reason the review loop exists at all: you can't trust generated code, so you keep a reviewer watching it. That leg gets the same treatment. Secure-coding defaults distilled from OWASP ASVS 5.0 ride inside the generation prompt -- allowlist validation, argument-vector subprocess calls, parameterized queries, path containment -- so the code is born to a standard instead of scanned into one. A deterministic semgrep and gitleaks gate checks what lands, and the security reviewers at the end are there to find nothing.

## Deterministic first: the economics

Every gate in that pipeline started life as a model call and got demoted. That demotion is the method.

The rule is simple: a signal moves to the cheapest layer that can produce it. Citation checking is exact string matching. Stub detection is import-and-patch analysis on the test file. Assertion gaming is a classified diff. Scope violations are a path comparison against an allowlist. None of these need intelligence; they need rigor, and code is more rigorous than a model at 11pm on the fortieth build of the week.

The economics compound. A review loop pays model prices on every lap, and the laps multiply just when the code is worst. A deterministic gate costs nothing per run, never rubber-stamps, and produces the same verdict on Friday night as on Monday morning. We spend model tokens in two places: writing the artifacts (spec, tests, implementation) and the one adversarial audit that needs judgment. Everything else is code checking code.

The rest of the loop's cost structure gets dismantled the same way. When a deterministic gate fails, a cheap model fronts the failure and drafts a directed repair; the gate re-runs and stays the authority, so the expensive model is spent once, on the build. A crashed build resumes from its event log and never repays a completed model call. And the spend is not vibes: cost and token rollups per run, phase, and cycle come straight from the same log.

This is also why the process stays fixed rather than agentic. Agent teams negotiate, and negotiation is where discipline leaks -- a team can agree to skip testing "just this once." A state machine can't. Phases run in order, gates block progression, and there is no conversation in which an agent talks the pipeline out of a check. In a year of building with this system, we've never once wished the agents could skip a gate. We've wished they were faster. Never less rigorous.

## The engine

The current core is a Python workflow engine, `engine_py/`, with zero runtime dependencies and no LLM vendor baked in. The pipeline diagram above is five boxes; underneath sit 27 workflow modules, phase 0 research through phase 8 post-deploy, including a spec-lite lane for small tasks, a DevOps pipeline, integrity and smoke phases, and a review fastpath for simple changes.

State is an append-only JSONL event log. There is no mutable state file to drift or race; the engine derives the current state of a build by replaying its events. It writes phase and step sentinels on success only, so if the process dies mid-build -- crash, laptop restart, network drop -- the run resumes from the log instead of starting over, and a sticky error can never replay as progress. A run never pays for a completed model call twice. Cost and token rollups per run, phase, and cycle come from the same log.

The anti-gaming lints ship as engine modules and run as code: stub-passability, the test-integrity diff guard, scope-inverse, spec citation and coverage checks, helper extraction, suite safety. The full inventory is in the appendix. The engine's own test suite is 300+ hermetic pytest tests -- no network, no API keys -- and CI installs the built wheel with no extras to prove the core runs on a bare Python install.

Backends are pluggable. Anthropic, Azure OpenAI, and Claude Code backends ship as references; wiring in your own model is about 20 lines. There is a keyless demo that walks a frozen spec through the full loop against a toy repository, gate rejection included, so you can watch the machinery without spending a cent.

## What works and what doesn't

**What works:** the combination. Frozen machine-verified specs, failing tests, an adversarial gate, read-only tests during implementation, deterministic lints throughout. No single technique is enough; each covers the others' gaps. The gates are what let me build in languages I can't read -- HalVoice is a native SwiftUI app and I have never written Swift. Our security agent BARK is 15,000 lines of Python with 3,500 tests, all generated through this pipeline. The tests are the QA team.

**What's hard:** architectural decisions still need a human. "Add email verification" works great. "Event sourcing or CRUD?" requires context the model doesn't have. Turning a business goal into a technical spec is still my job; the pipeline starts at "here's what to build."

**Speed:** not fast. A feature build takes 30-45 minutes, complex ones longer. But "fast generation plus 45 minutes of manual review" tends to lose to "slow generation plus zero review." Total time is the metric that matters, and the token bill is part of it.

**Honest limitations:** single operator, no team collaboration features. Requires bounded scope -- "build me a product" doesn't work, "add a rate limiter to the auth endpoint" does. The engine is young as an open source project; the extraction from our internal system is recent and the edges show.

## Try it

ByteDigger is open source, MIT. The engine installs as a plain Python package:

```bash
git clone https://github.com/guy-lifshitz/bytedigger
cd bytedigger/engine_py
pip install -e .
python3 ../examples/verified-tdd-run/run_demo.py   # keyless, end to end
```

The same discipline also ships as a Claude Code plugin:

```bash
claude plugin marketplace add guy-lifshitz/bytedigger
claude plugin install bytedigger@bytedigger
/build "add email verification"
```

The method -- frozen verified specs, failing tests first, an adversarial gate, deterministic anti-gaming lints -- isn't tied to any vendor. If your agents write code, they need external validation, and most of that validation should be code, not another model.

Break it, fork it, tell us what gates are missing.

[github.com/guy-lifshitz/bytedigger](https://github.com/guy-lifshitz/bytedigger)

## Appendix: the machinery

The article describes the shape. This is the inventory, for anyone deciding whether the engine is a weekend wrapper or a machine. Paths are relative to `engine_py/`.

**Deterministic gates (no LLM in the loop):**

- `spec_cite` -- every path:line citation in a spec checked against the repository before freeze
- `stub_passability` -- rejects a RED test that mocks its own unit under test
- `scope_inverse` -- catches implementation writes outside the spec's file allowlist
- `token_consistency` -- one drifted literal (an event name spelled two ways) across a spec kills a build hours later; caught at freeze instead
- `presence_triad`, `reentry_ac`, `suite_safety`, `forbidden_import` -- each one a codified post-mortem
- `net_new_delta.py` -- the RED-to-GREEN delta must be net-new passes, not reshuffled counts
- `reproducibility.py` -- the test command runs five times and must produce identical pass/fail counts before the engine enforces any delta. Flaky suites don't get to vote.

**Durability:**

- append-only event log with end-to-end replay (`event_log.py`) -- the engine derives state by replay; there is no mutable state file
- success-only phase and step sentinels (`lib/phase_sentinel.py`, `lib/step_sentinel.py`) -- a crashed build resumes mid-pipeline; a sticky error can't replay as progress
- optional DBOS durable backend with run listing and status
- restart governor (`lib/restart_governor.py`) -- caps phase re-invocation, and knows a crash-start from a gate-start
- bounded spawn chokepoint (`lib/bounded_spawn.py`) -- every subprocess carries a mandatory timeout; an unbounded hang is impossible by construction
- cost and token rollup per run, phase, and cycle from the event log (`lib/cost_rollup.py`)
- reject and spec-defect ledgers (`reject_log.py`, `spec_defect_ledger.py`) -- every REVISE and FAIL reason is durable JSONL, with a bounded reroute budget for defective specs

**Anti-gaming verification:**

- citation verifier (`lib/plugins/anti_hallucination/`) -- checks every reviewer claim of the form path:line: quote against the file on disk, demotes fabricated findings to an Unverified section, and recomputes the verdict without them
- disk truth (`lib/plugins/disk_truth/`) -- RED and GREEN claims verified against the real git diff and real test-runner subprocess output, not the agent's say-so
- verdict cross-check (`verdict_verify.py`) -- anchor-hash and AC-parity check of self-attested LLM verdicts against on-disk files
- directed repair (`lib/directed_repair.py`) -- a cheap model fronts each gate's FAIL branch to draft the fix; the gate re-runs and remains the correctness authority

**Learning and injection:**

- the phase 7 synthesizer extracts categorized learnings from every build into a pluggable store. The default backend is plain markdown files under `.bytedigger/learnings/`; a reference SQLite shell backend, with its schema and tests, ships as the worked example of plugging in your own
- the injection step (`workflows/phase_05_inject.py`) defines the injection contract -- a folder assembled before implementation: matched learnings from the configured store, a project constitution discovered by precedence, quality-gate and security rules, active-work context. Bring your own memory backend; the contract is what's fixed

**Security:**

- secure-coding defaults distilled from OWASP ASVS 5.0 ride inside the generation prompt (`security/secure-codegen-rules.md`): allowlist validation, argument-vector subprocess calls, parameterized queries, path containment
- deterministic semgrep + gitleaks gate (`security/security_lint.py`, with `semgrep-rules.yml` and `gitleaks.toml`) lints what lands
- the review panel fields an OWASP Top 10 security reviewer; a detected DevOps artifact adds a CIS/OWASP/SLSA devops reviewer (`workflows/phase_6_review.py`)
- mypy baseline gate (`lib/mypy_baseline.py`, wired into phase 5): a change that adds new type errors does not pass
- phase 0.6 detects DevOps artifact types among the changed files (Dockerfile, Kubernetes manifest, Terraform, CI config) and routes the build into a fail-closed security scan (`workflows/phase_5_devops_scan.py`) whose allowlist waivers carry expiry dates
- commit gate (`audit_gate.py`) -- a commit-msg hook blocks any commit touching engine production code unless an APPROVED audit document is co-staged

All of it sits in 27 workflow modules, phase 0 research through phase 8 post-deploy. None of it is speculative: every gate on this list exists because something got through without it.
