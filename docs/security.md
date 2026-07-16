# Threat model

What the engine trusts, what it checks, and what you have to isolate
yourself. Short version: the gates keep generated code honest, the deployment
environment keeps it contained. Those are different jobs and this project only
does the first one.

## The engine executes LLM-generated code

That is the whole point of the GREEN phase: a model writes an implementation
and the engine runs your test suite against it. Test execution runs
subprocesses; agentic backends can write files and run commands in the
workspace. Treat every run the way you would treat running a stranger's pull
request locally.

The deployment assumption is an isolated workspace. A container is the clean
answer; a dedicated git worktree with nothing valuable in reach is the
pragmatic one. The agentic pydantic backend restricts bash to an argv0
allowlist executed without a shell, and its write manifest comes from a
pre-state-aware git diff rather than model self-report -- both are accident
protection. Neither is a security boundary, and the docs have said so from day
one. A model that wants to escape a test subprocess on an unisolated host has
plenty of room.

## Credentials

API keys enter through environment variables only (`ANTHROPIC_API_KEY`,
`AZURE_OPENAI_KEY`, the rest are in [backends.md](backends.md)). Nothing reads
keys from files in the workspace, nothing writes them anywhere, and prompts
are assembled from repo content -- so a key can only leak into a prompt if you
commit it into the repo first. Backend error paths truncate provider responses
rather than echoing request headers.

The event log records step names, statuses, durations, byte counts, and
artifact paths. It does not record env vars or request payloads. It is still
an append-only file in the workspace: if your spec or repo content is
sensitive, the log inherits that sensitivity, so ship it into bug reports with
the same care as the repo itself.

## What the gates are, and are not

The deterministic lints (stub-passability, test-integrity diff guard,
scope-inverse, the spec lints) defend one specific thing: the integrity of the
verification loop against the agent inside it. They assume the operator is
honest and the model is lazy, sloppy, or reward-hacking. They do not assume
the model is malicious, and they make no attempt to stop code that is. Gate
bypasses are security bugs and belong in a private report (see
[SECURITY.md](../SECURITY.md)); host escapes from generated code are an
isolation problem on your side of the line above.

## What gets published

The public package is exactly the manifest-driven core extracted into this
repository -- engine, gates, workflows, reference backends, plugin files.
There is a private superstructure upstream (orchestration, memory, fleet
tooling); it is not published here and there is no commitment that it ever
will be. Nothing in the core phones home, and the only network calls are the
ones your configured LLM backend makes to its provider.
