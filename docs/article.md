# Shrinking the Human in the Loop

> *A year of figuring out how to make AI reliably check AI code, one gate at a time.*

*Guy Lifshitz*

---

Everyone talks about AI writing code. That's not news anymore. The real problem starts one step later: someone has to check that code. The industry's answer is a loop -- generate, review, fix, review again -- with either a human or another model turning the crank. The loop is expensive, it converges slowly, and when the reviewer is also a model, it shares the writer's blind spots.

We took a different bet. ByteDigger is built to kill that loop, not to automate it. Checks move to before the code exists: the spec freezes first and gets machine-verified against the real codebase, failing tests land and get adversarially audited before a single line of implementation, and the checks that matter run as cheap deterministic code, not as more LLM judgment. Reviewers still run at the end. The design goal is that they find nothing.

Two principles drive every decision, and they turn out to be the same decision. Quality first: an agent must not be able to game its own acceptance signal, ever. Economics first: every check runs at the cheapest layer that can produce it -- regex, AST, a diff, a byte count -- and a model gets called only when code genuinely cannot decide. We do not burn tokens on judgment where a grep gives the same answer. The vendors are happy to sell you a review loop that laps forever; a regex gate costs nothing and never gets tired.

Here's how we got there.

## The bottleneck

I'd ask Claude for a feature, get 400 lines back, and spend an hour reading every one of them. Net time saved: maybe 30 minutes on a good day. So naturally you think: let AI review AI code. Except that's where it gets ugly. AI reviewing AI produces what I call assertion theater. Tests that technically pass but verify nothing. Reviews that say "looks good" without catching real issues. The writer and the reviewer collude, not out of malice, but because they share the same blind spots.

Most agentic coding failures are not capability failures. They are verification failures. That reframing took me an embarrassingly long time, and it changed what we built.

## Specs: not a failure, but prose isn't enough

My first approach was spec-driven development. Write detailed specs -- user stories, data models, interfaces -- then generate code that matches them. The theory: constrain the AI enough and the output will be correct.

Specs absolutely matter. Without them, AI hallucinates architecture. But prose specs hit two walls. First, you're still the bottleneck, just at a different stage: writing good specs takes nearly as long as writing code. Second, and worse, AI-generated specs drift from the actual codebase. References to functions that don't exist, quoted signatures that were true three commits ago, interfaces that conflict with what's already there. A spec that cites fiction produces tests that assert fiction, and everything downstream inherits the lie.

So the spec stopped being a document and became an artifact the machine verifies. Two mechanisms:

**Citation verification.** Before a spec freezes, a deterministic lint checks every citation in it against the real repository. Quoted function signatures must match the actual source. Named symbols must resolve to real files. A cited line must exist where the spec says it does. This is a lint, not a model call: exact matching with a windowed search for drift. A spec that references code that isn't there gets rejected at write time, hours before any reviewer would have noticed.

**Acceptance criteria that compile.** Alongside the prose, each spec carries a machine-readable block that maps every acceptance criterion to a mechanical check from a closed registry: file contains this string, command exits zero, and so on. The block is validated at freeze time and executed as code, so "done" means the AC table passes, not that somebody's judgment felt satisfied. A criterion that genuinely needs judgment has to declare itself as one, which keeps the escape hatch visible instead of ambient.

Specs stop being documentation that drifts. They compile.

## TDD: right idea, but AI cheats

Tests are binary. Pass or fail. No subjective judgment. So the pipeline is strict TDD.

Quick primer for anyone outside the TDD world: the RED phase writes tests that FAIL, before any implementation exists. The GREEN phase writes only enough code to turn them green. If tests pass immediately in RED, they aren't testing anything real.

This worked for about a week. Then patterns emerged. `expect(true).toBe(true)` -- assertion theater. Tests that checked mocks instead of behavior. And my personal favorite: "tests aren't needed for this simple change" -- rationalization from an agent that wanted to skip the hard part.

But the real killer was assertion gaming. Kent Beck has talked about this. Even the best models do it: when a test fails in GREEN, the model changes the test assertion to match reality instead of fixing the code. API returns 404? Instead of fixing the endpoint, it updates the test to expect 404. Done -- tests pass, feature is broken. This is not a prompting failure you can fix with better instructions. Models optimize for "make tests pass," not "make code correct." The only fix is external validation, from outside the agent's context.

One more failure mode deserves its own name: the vacuous RED. An agent writes a test file that imports the unit under test and then mocks that same unit inside the test. The test fails before implementation, passes after, and verifies nothing at all -- it exercises the mock. We got burned by this exactly once. Now a deterministic lint catches it: a symbol that is both imported and patched in the same test file is an automatic reject, no model involved.

## The loop, killed

Here's the shape of the pipeline now:

```
spec (frozen, machine-verified) -> RED (failing tests) -> gate (adversarial audit) -> GREEN (implement) -> verify
            ^                            |
            +--------- REJECT -----------+
```

**Spec freezes first**, with an AC table and a file allowlist. Citation lint and AC validation run before the freeze. Scope drift dies at write time.

**RED lands failing tests.** The engine independently runs them and checks that they fail -- it does not take the agent's word for it. The deterministic red lints run here: stub-passability, fixture checks, collection health.

**The gate audits adversarially.** A separate, stronger model validates the tests against the spec: every acceptance criterion maps to a test, every test maps back to a criterion, assertions exercise real behavior. The gate cannot write or modify tests. It returns a verdict, and REJECT routes back to the test writer. It rejects a lot. That is the point -- a vacuous test gets killed while killing it is still cheap.

**GREEN implements with the tests read-only.** A diff guard compares the test files before and after implementation and classifies every change. Assertion gaming is a hard fail. A scope lint flags any write outside the spec's file allowlist.

**Verify runs the suite** -- the full one, not the convenient subset. Passing a scoped run while breaking the tree is one of the classic cheats, so the engine treats "which tests ran" as part of the signal.

Notice what is absent: the generate-review-fix loop. There is nothing to babysit, because the problems the loop exists to find are removed before the code exists. When a build finishes, the review agents at the end are a safety net, not a workflow.

## Deterministic first: the economics

Every gate in that pipeline started life as a model call and got demoted. That demotion is the methodology.

The rule is simple: a signal moves to the cheapest layer that can produce it. Citation checking is exact string matching. Stub detection is import-and-patch analysis on the test file. Assertion gaming is a classified diff. Scope violations are a path comparison against an allowlist. None of these need intelligence; they need rigor, and code is more rigorous than a model at 11pm on the fortieth build of the week.

The economics compound. A review loop pays model prices on every lap, and the laps multiply exactly when the code is worst. A deterministic gate costs nothing per run, never rubber-stamps, and produces the same verdict on Friday night as on Monday morning. We spend model tokens in exactly two places: writing the artifacts (spec, tests, implementation) and the one adversarial audit that needs judgment. Everything else is code checking code.

This is also why the process is fixed rather than agentic. Agent teams negotiate, and negotiation is where discipline leaks -- a team can agree to skip testing "just this once." A state machine can't. Phases run in order, gates block progression, and there is no conversation in which an agent talks the pipeline out of a check. In a year of building with this system, we've never once wished the agents could skip a gate. We've wished they were faster. Never less rigorous.

## The engine

The current core is a Python workflow engine, `engine_py/`, with zero runtime dependencies and no LLM vendor baked in. Phases are registered workflows: research, spec, implement (the RED/gate/GREEN loop), review, synthesize.

State is an append-only JSONL event log. There is no mutable state file to drift or race; the current state of a build is derived by replaying its events. If the process dies mid-build -- crash, laptop restart, network drop -- the run resumes from the log instead of starting over. Completed model calls are never paid for twice.

The anti-gaming lints ship as engine modules and run as code: stub-passability, the test-integrity diff guard, scope-inverse, spec citation and coverage checks, helper extraction, suite safety. The engine's own test suite is 300+ hermetic pytest tests -- no network, no API keys -- and CI installs the built wheel with no extras to prove the core runs on a bare Python install.

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

The methodology -- frozen verified specs, failing tests first, an adversarial gate, deterministic anti-gaming lints -- isn't tied to any vendor. If your agents write code, they need external validation, and most of that validation should be code, not another model.

Break it, fork it, tell us what gates are missing.

[github.com/guy-lifshitz/bytedigger](https://github.com/guy-lifshitz/bytedigger)
