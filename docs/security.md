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
are assembled from repo content and the operator's `role_template_path` file --
so a key can only leak into a prompt if you commit it into the repo or put it in
that file first. Pointing `role_template_path` into the workspace is a
gate-integrity misconfiguration: an agent-writable template steers later gates
(see [configuration.md](configuration.md#role-template-engine)). Backend error
paths truncate provider responses rather than echoing request headers.

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

## Subagent write guard

`hooks/worker-write-guard.sh` is a PreToolUse hook on `Write|Edit|MultiEdit|NotebookEdit`.
While a build is active (`build-state.yaml` in the working directory has a
`current_phase` other than `completed`) it enforces two things for subagent calls:
no subagent may write `build-state.yaml`, `build-metadata.json`, `build-red-output.log`,
`build-green-output.log` or `.bytedigger-orchestrator-pid` (names compared
case-insensitively, symlinks and hardlinks resolved), and the `synthesizer` role may write
only under `<scratchpad_dir>/reviews/`.
The protected-name and per-role rules (R5–R7) never apply to the orchestrator (main thread);
it is blocked only when the tool input is malformed. If the tool input is unreadable during
an active build, or the check itself fails, the hook blocks (fail closed).

Known limits, not fixed by this hook:

- Bash writes. The hook sees file tools only; a general worker with Bash can still
  write with `echo > build-state.yaml`. The synthesizer has no Bash.
- Orchestrator `cd` into a subdir that persists. The hook reads `build-state.yaml` only
  from the working directory, so with no state file there the guard is off.
- Stale state after a worktree build. A `build-state.yaml` left in the main checkout
  keeps the guard on there. A FAILED build also keeps `build-state.yaml`, so the guard
  stays on in that checkout until it is cleaned up.
- engine_py runs on any backend, including API-token backends. It keeps no
  `build-state.yaml` and fires no plugin hooks, so it gets no subagent guard. Those runs rely on
  the engine's own write manifest and test-integrity diff guard (above); `build-gate.sh`
  covers only the plugin path. Engine workers started as
  `claude -p` are separate main-thread sessions, not subagents.
- NTFS alias names (trailing dot or space, 8.3 short names) are not recognised as the
  protected file names.
- Any host or runner that calls the hook without agent_id is treated as the main
  thread and allowed (for example Claude Code older than 2.1.69).
- Without python3 on PATH the hook allows everything and prints a WARN line.

## What gets published

The public package is exactly the manifest-driven core extracted into this
repository -- engine, gates, workflows, reference backends, plugin files.
There is a private superstructure upstream (orchestration, memory, fleet
tooling); it is not published here and there is no commitment that it ever
will be. Nothing in the core phones home, and the only network calls are the
ones your configured LLM backend makes to its provider.
