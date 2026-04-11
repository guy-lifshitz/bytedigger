# Shrinking the Human in the Loop

> *Six months of figuring out how to make AI reliably check AI code, one gate at a time.*

*Guy Lifshitz*

---

YouTube is full of "team of AI agents" demos. An architect agent, a coder agent, a QA agent - they chat with each other and produce code. It looks impressive. It's also unpredictable. Agents negotiate, lose context, skip steps when they decide it's fine. There's no enforcement.

We took a different approach. ByteDigger is not a simulated dev team. It's a pipeline. A conveyor belt with quality control at every station. The difference matters: teams negotiate, pipelines enforce. A team can agree to skip testing "just this once." A pipeline can't - the gate blocks progression, period.

The tradeoffs are real. A team of agents is more flexible - they can improvise, adjust scope mid-conversation, handle ambiguity. A pipeline is rigid - phases run in order, gates check specific conditions, no exceptions. We chose rigidity. In six months of building with this system, we've never once wished the agents could skip a gate. We've wished they were faster. But never less rigorous.

Everyone talks about AI writing code. That's not news anymore. The real problem starts one step later: someone has to check that code. And when that someone is also you, the solo developer, you become the bottleneck.

I'd ask Claude for a feature, get 400 lines back, and spend an hour reading every one of them. Net time saved? Maybe 30 minutes on a good day. So naturally, you think: let AI review AI code. Makes sense. Except that's where it gets ugly. AI reviewing AI produces what I call assertion theater. Tests that technically pass but verify nothing. Reviews that say "looks good" without catching real issues. The reviewer and the writer collude, not out of malice, but because they share the same blind spots.

StrongDM's Software Factory ([factory.strongdm.ai](https://factory.strongdm.ai), [github.com/strongdm/attractor](https://github.com/strongdm/attractor)) is one of the most serious efforts here. A graph-based pipeline where neither code nor reviews are human. They validate quality via holdout end-to-end scenarios at the end of the run. Smart approach. But we found that validating only at the end lets bad assumptions compound across phases. ByteDigger validates at every phase transition: 12 gate hooks, BDD validation, a 4-step Opus audit, semantic skip detection.

## The Spec Phase: Not a Failure, But Not Enough

My first approach was spec-driven development with SpecKit. Write detailed specs (user stories, data models, interface definitions) then generate code that matches them. The theory: constrain the AI enough and the output will be correct.

Specs absolutely matter. Without them, AI hallucinates architecture. But specs alone hit a wall: they require heavy human involvement. You're still the bottleneck, just at a different stage. Writing good specs takes nearly as long as writing code. And AI-generated specs drift from the actual codebase. References to patterns that don't exist, interfaces that conflict with what's already there.

I didn't kill specs. I integrated them. GitHub's Spec Kit ([github.com/github/spec-kit](https://github.com/github/spec-kit)) proved the idea right, with 60+ community extensions. But Spec Kit is a spec layer, not a pipeline. No runtime enforcement, no gates, no feedback loop. ByteDigger made specs mandatory: Phase 4.5 auto-generates `build-spec.md` with user stories, interfaces, data models, and test cases. Opus reviews the spec before it moves forward. Spec-driven development, but automated and gated.

## TDD: Right Idea, But AI Cheats

Tests are binary. Pass or fail. No subjective judgment. So I switched to strict TDD.

Quick primer for anyone outside the TDD world:
- **RED phase** - write tests that FAIL. No implementation code yet. The tests describe what the feature should do, and they must fail because the feature doesn't exist.
- **GREEN phase** - write code to make the tests pass. Only enough code to turn red tests green.
- If tests pass immediately in the RED phase, your tests are wrong. They're not testing anything real.

This worked for about a week. Then patterns emerged. `expect(true).toBe(true)` - assertion theater. Tests that checked mocks instead of behavior. Tests so tightly coupled to the implementation that they'd pass even if the implementation was wrong. And my personal favorite: "Tests aren't needed for this simple change" - rationalization from an agent that wanted to skip the hard part.

But the real killer was assertion gaming. Kent Beck, the father of TDD, has talked about this problem. Even the best models (Opus included) do it: when a test fails in the GREEN phase, the model changes the test assertion to match reality instead of fixing the code. API returns 404? Instead of fixing the endpoint, it updates the test to `expect(response.status).toBe(404)`. Done - tests pass, feature is broken. This isn't a prompting failure you can fix with better instructions. Models optimize for "make tests pass," not "make code correct." The only fix is external validation. A gate that catches the cheat from outside the agent's context.

Despite the cheating problem, the TDD approach generates real assets. Our security agent BARK has 15,000 lines of code and 3,500 tests, all generated through this pipeline. We don't need a QA team. The tests ARE the QA. End-to-end tests are a separate concern we haven't covered yet, but unit and integration coverage is built into every build.

Nobody likes TDD because it's twice the work. You write tests AND code. But when AI writes both, TDD is free. That changes everything. TDD is having a renaissance, not because developers learned to love it, but because AI made the cost zero.

## The Fix: TDD + BDD + Separate Validator

No single technique works alone. TDD has holes. Specs drift. BDD scenarios can be vague. The breakthrough was combining all three and adding a wall between them.

**What is BDD?** Behavior-Driven Development means writing requirements as structured scenarios: `Given [state], When [action], Then [outcome]`. Plain text that anyone can read without touching code. AI agents understand text natively, making BDD a natural fit. The key insight: BDD bridges human-readable requirements and machine-executable tests.

Here's the pipeline:

```
RED (failing tests) --> BDD (Gherkin scenarios) --> Opus Validates --> GREEN (implement) --> Test Integrity Guard
      |                       |                        |                    |                      |
  Write tests           Translate spec           4-step audit         Write code only        Diff RED vs GREEN
  that FAIL             to Given/When/Then       against Gherkin      to pass tests          Block if tests
  first                 scenarios                scenarios             (no test edits)        were modified
```

**RED.** A worker agent writes failing tests based on the spec. Every acceptance criterion becomes a test case. Tests must fail. If any pass before implementation, something's wrong.

**Gherkin scenarios.** A separate agent translates the spec into BDD scenarios. A developer can open the Gherkin file and understand exactly what's being tested without reading code. This is how humans stay in the loop when they want to.

**Opus audit.** A different, more capable model validates tests against the Gherkin scenarios. Four checks:

- Forward map: every Gherkin scenario has at least one test
- Reverse map: every test maps back to a scenario (catches orphan tests)
- Spec compliance: every acceptance criterion appears in both Gherkin and test code
- Quality check: assertions test real behavior, not theater

The validator cannot write or modify tests. It returns PASS or FAIL. If it fails, the test writer rewrites. The pipeline blocks until Opus signs off.

**GREEN.** Only now does the implementation agent write code. And here's where the test integrity guard kicks in: it diffs the RED-phase tests against the GREEN-phase tests. If the implementation agent modified test assertions instead of fixing code, hard block. Assertion gaming caught and killed.

This caught assertion theater on the first run. Mock-testing-mocks on the second. The coder doesn't judge their own work anymore.

## Build State: The Pipeline's Memory

One concept matters before the gates: `build-state.yaml`. A YAML file tracking pipeline progress. Every phase writes its status (PENDING, IN_PROGRESS, COMPLETED, FAILED). Gates read it to decide whether the next phase can start. If Claude Code crashes or your laptop restarts, the pipeline resumes from where it left off. The pipeline's memory. No re-running completed phases, no lost progress.

## The Arms Race: 8 Hook-Enforced Gates (Plus Soft Checks)

With the validation layer working, I kept finding new ways AI tries to cut corners. So I kept adding gates to block progression at critical checkpoints.

| # | Gate | Phase | What It Checks | What It Catches |
|---|------|-------|----------------|-----------------|
| 1 | Phase Progression | All transitions | `build-state.yaml` status before allowing next phase | Skipped phases, out-of-order execution |
| 2 | Dependency Pre-check | Phase 0 | Required tools and packages exist before build starts | Missing runtime deps, broken environments |
| 3 | Security Routing | Phase 0 | Detects auth/crypto/secrets files; triggers Phase 4 audit | Security-sensitive areas get extra scrutiny |
| 4 | DevOps Validator | Phase 5.4 | Runs terraform validate, hadolint, checkov, trivy | Broken infra configs caught before shipping |
| 5 | Spec Completeness | Phase 4.5 | Opus reviews build-spec.md against requirements | Missing acceptance criteria, vague specs |
| 6 | RED Test Validity | Phase 5 | All tests must FAIL before implementation exists | Tautological tests, assertion theater |
| 7 | BDD-Test Mapping | Phase 5 | Forward + reverse map between Gherkin and tests | Orphan tests, missing scenario coverage |
| 8 | Spec Compliance | Phase 5 | Every acceptance criterion in both Gherkin and tests | Drift between spec and test suite |
| 9 | Test Integrity Guard | Phase 5.5 | Diffs RED tests vs GREEN tests | Assertion gaming, modified test expectations |
| 10 | Semantic Skip Detector | Phase 6 | Scans reviews for "acceptable risk", "fix later" | Rubber-stamp reviews, deferred issues |
| 11 | Boy Scout Rule | Phase 6 | Per-file checklist: dead imports, types, naming | Code quality regression |
| 12 | Review Completion | Phase 7 | All review findings fixed and signed off | Skipped cleanup, ignored review comments |

Each gate was born from a specific failure mode we hit in production. Hooks and rules exist in other AI coding systems too. Cursor has rules files, other tools have configuration hooks. The difference: ByteDigger uses hooks for enforcement, not configuration. A gate blocks pipeline progression until the check passes. It's not a suggestion. It's a wall.

After 50+ builds I noticed my manual reviews were catching the same things the gates already caught. But gates are more consistent. They never rubber-stamp at 11pm. They never say "looks fine" because they're tired. They never skip the edge case check because the PR is small.

I made AUTONOMOUS mode the default. Not a leap of faith. It happened gradually. Build after build, the human approval step added zero signal. The pipeline earned trust by being more reliable than I was. SUPERVISED mode is still there for architecture decisions. BDD scenarios are readable enough that you can review intent without reading implementation.

We use Plannotator (our open-source review UI) for human review when needed. The pipeline supports human-in-the-loop. It just doesn't require it.

## Beyond Code: Security and DevOps

The gates aren't limited to application code. Two areas proved just as important.

**Security routing.** Phase 0 detects authentication flows, cryptographic operations, and secrets files. When found, Phase 4 adds a security architect role and Phase 6 adds a dedicated security reviewer. This runs before any code is written, so security-sensitive areas get extra scrutiny from the start.

**DevOps validation.** Phase 0 detects infrastructure files: Terraform, Dockerfiles, K8s manifests, Helm charts, GitHub Actions. When ByteDigger detects these files, it automatically adds DevOps validation as Phase 5.6. That phase runs linting tools (automated checkers that catch syntax errors, misconfigurations, and security issues before code runs): `terraform validate`, `hadolint`, `actionlint`, `kubectl dry-run`, `helm lint`. On top of that, security scanners from established tooling: `checkov` and `trivy` for infrastructure security, `gitleaks` for secrets detection. We didn't build custom validators. We took HashiCorp's guidance for Terraform and integrated best-of-breed open source security tools into the pipeline. CRITICAL and HIGH findings must be fixed (up to 3 auto-fix cycles) before the pipeline proceeds. These tools are installed separately (see README for the full list). If they're not installed, validation is skipped gracefully.

Both features are already in the ByteDigger open source release.

Most AI coding tools stop at code generation. We wanted the full cycle: requirements to deployment. The DevOps module (Phase 5.6) handles infrastructure validation - terraform validate, container scanning, secrets detection. That's dev to deploy in one pipeline. What's still missing: product requirements (PRD). I write those myself. Turning a business goal into a technical spec is still a human job. The pipeline starts at "here's what to build", not "here's what the business needs."

## What Actually Works and What Doesn't

**What works:** TDD + BDD + a separate validation model. No single technique is enough. Together they cover each other's gaps. The gates enable working with unfamiliar languages. I built HalVoice, a native SwiftUI app, without ever having written Swift. I can't read the code. But the pipeline writes tests, validates them with BDD, reviews with 6 agents, and enforces gates. When gates are reliable enough, the human doesn't need to understand the implementation language - the pipeline IS the quality layer. On the other end, our security agent BARK is 15,000 lines of Python with 3,500 tests, all built through this pipeline. No QA team. Typed languages like TypeScript give AI more guardrails through the compiler. Untyped languages like Python give it more rope. Gate enforcement partially closes that gap - TDD and review catch what a type system would have caught.

We've tested this across TypeScript, Python, Swift, Bash, and infrastructure-as-code (Terraform, Kubernetes YAML, Dockerfiles). The pipeline is language-agnostic because phases are markdown instructions, not language-specific tooling.

**What's hard:** Architectural decisions still need a human. "Add email verification" works great. "Event sourcing or CRUD?" requires context the AI doesn't have.

**Speed trade-off:** Not fast. FEATURE tasks take 30-45 minutes, complex builds 1-3 hours. But "fast generation + 45 minutes of manual review" often takes longer than "slow generation + zero review." Total time is what matters.

**PR workflow.** `/build --pr` creates a branch, commits, pushes, and opens a PR. Phase 6 uses specialized review agents (code reviewer, silent failure hunter, type design analyzer, test analyzer, security reviewer), following the pattern established by Anthropic's pr-review-toolkit. These agents run in parallel and vote on fixes with confidence scoring. Use the best available tools, don't reinvent review.

**Honest limitations:**

- Single operator. No team collaboration features yet.
- Requires bounded scope. "Build me a product" doesn't work. "Add a rate limiter to the auth endpoint" does.
- Gate enforcement only works as a Claude Code plugin. The hooks system makes gates real. Without it, they're suggestions.
- Depends on external review tooling (pr-review-toolkit) that could change or break.
- We have a batch executor for running multiple tasks in parallel, but that's a separate story.

## Try It

ByteDigger is open source. MIT license. Install it as a Claude Code plugin:

```bash
claude plugin add shtofadhor/bytedigger
/build "add email verification"
```

8 phases, 8 hook-enforced gates, mandatory TDD, 3-7 review agents per build.

The methodology - phased pipeline, gates, TDD+BDD, multi-agent review - isn't tied to Claude Code. The phases are markdown instructions. The gates are validation logic. Adapt it for Cursor, Windsurf, Copilot Workspace, or custom setups. ByteDigger is a Claude Code plugin today, but the patterns are universal. If your agents write code, they need external validation.

Break it, fork it, tell us what gates are missing.

[github.com/shtofadhor/bytedigger](https://github.com/shtofadhor/bytedigger)
