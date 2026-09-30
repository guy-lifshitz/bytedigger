# bd#133 — PreToolUse path guard for subagent Write/Edit

**Status: FROZEN Rev 1** (before gate) · **Class:** SYSTEMATIC · **Chokepoint:** one new PreToolUse hook,
`hooks/worker-write-guard.sh`, registered in `hooks/hooks.json` for
`Write|Edit|MultiEdit|NotebookEdit`. Every file write a subagent makes through a file tool
passes it.

## §0 Host behaviour verified (Claude Code 2.1.286, 2026-09-30)

A `--settings` PreToolUse hook on `Write|Edit` fired for a Write made by a `general-purpose`
subagent; stdin carried `agent_id` (non-empty) and `agent_type: "general-purpose"`, plus `cwd`
and `tool_input.file_path` (absolute). The same hook with `exit 2` + a stderr line stopped the
write (no file on disk) and the subagent reported the stderr text. Hook docs: `agent_id` /
`agent_type` are present only in a subagent context; plugin agents are named
`<plugin>:<agent>`. Fields added in Claude Code 2.1.69: on older hosts every call looks like a
main-thread call, so the guard allows (degrades to today's behaviour).

## §1 Problem (measured on `44a70b9`)

1. #127 (PR #134) gave `explorer`, `architect`, `synthesizer` the `Write` tool
   (`agents/*.md:4`). The limit "write only your one deliverable" is prompt text
   (`## Deliverable`, phases 2/4/7).
2. Other workers (tdd-worker, fixers, reviewers — spawned as general-purpose) hold
   Write/Edit under prompt-only limits too.
3. `build-state.yaml` carries gate verdicts (`review_complete`, `opus_validation`,
   `plan_review`) that `build-gate.sh` trusts. `build-metadata.json` pins complexity
   ("MUST NOT be modified after Phase 0", `phase-0-classify.md:39`). Nothing deterministic
   stops a subagent from writing either one.
4. The only PreToolUse hook, `hooks/build-state-guard.sh`, matches `Bash` and blocks deletion
   only.

## §2 Scope

In: a new hook + its registration + docs (`docs/plugin.md` hooks table, CHANGELOG) + tests.
Out (stay open as a new follow-up issue, listed in the PR): the three "lower priority" notes
of #133 (per-phase `gate_block_counter` reset, declarative phase→deliverable table, one shared
learnings parser). Bash writes by subagents (`echo > build-state.yaml`) are **out of scope**:
the read-only roles have no Bash; closing Bash for general workers is a different mechanism
(noted as a limit in `docs/security.md`, not fixed here).

## §3 Inputs the hook reads

- stdin JSON (Claude Code PreToolUse): `tool_name`, `tool_input.file_path`
  (`tool_input.notebook_path` for NotebookEdit), `cwd`, and — only when the call comes from a
  subagent — `agent_id` and `agent_type`.
- `<cwd>/build-state.yaml`: `current_phase`, `scratchpad_dir`. `cwd` from stdin; if absent,
  the process CWD.

Definitions:
- **Build active** ⇔ `<cwd>/build-state.yaml` exists and `current_phase` is non-empty and not
  `completed`. (Phase 7 is active: the synthesizer runs there.)
- **Subagent call** ⇔ stdin has a non-empty `agent_id`. Main-thread calls (the orchestrator)
  have none and are never blocked by this hook.
- **Role** = `agent_type` with any `<plugin>:` prefix removed (`bytedigger:explorer` →
  `explorer`). **Read-only roles** = `explorer`, `architect`, `synthesizer`.
- **Target** = `file_path` made absolute against `cwd`, then normalized with symlinks resolved
  (`os.path.realpath`), so `..` and symlinked dirs cannot escape.
- **Allowed dirs** for read-only roles = `realpath(<scratchpad>/research)`,
  `…/architecture`, `…/reviews`, where `<scratchpad>` = `scratchpad_dir` made absolute
  against `cwd`. A target is inside a dir iff it equals the dir + `/` prefix match (no
  sibling-prefix match: `research-evil/` is outside).

## §4 Decision table (first matching row wins)

| # | Condition | Result |
|---|---|---|
| R1 | `tool_name` not in {Write, Edit, MultiEdit, NotebookEdit} | allow (exit 0) |
| R2 | build not active | allow |
| R3 | stdin not valid JSON, or no target path | **block** (build is active; fail closed) |
| R4 | not a subagent call | allow |
| R5 | basename(target) is `build-state.yaml` or `build-metadata.json` (any dir) | **block** |
| R6 | role is read-only and `scratchpad_dir` is missing/empty | **block** |
| R7 | role is read-only and target is not inside an allowed dir | **block** |
| R8 | otherwise | allow |

R5 is checked on both the raw and the resolved basename (a symlink named `notes.md` →
`build-state.yaml` is blocked).

Block = exit 2, one line on stderr (Claude Code feeds stderr back to the agent) and the same
line on stdout, prefix `BLOCKED (bytedigger write guard): `. Exact reasons:
- R3: `BLOCKED (bytedigger write guard): unreadable tool input during an active build`
- R5: `BLOCKED (bytedigger write guard): subagents may not write <basename>; it is orchestrator state`
- R6: `BLOCKED (bytedigger write guard): <role> may write only its scratchpad deliverable, but build-state.yaml has no scratchpad_dir`
- R7: `BLOCKED (bytedigger write guard): <role> may write only under <scratchpad>/{research,architecture,reviews}/, not <target>`

python3 absent → allow with one stderr line
`WARN (bytedigger write guard): python3 not found; write guard disabled` (same degrade policy
as `build-state-guard.sh`; `docs/plugin.md` already lists python3 as optional for the guard).

## §5 Acceptance

- A1 `hooks.json` has a PreToolUse entry, matcher exactly `Write|Edit|MultiEdit|NotebookEdit`,
  command `${CLAUDE_PLUGIN_ROOT}/hooks/worker-write-guard.sh`, timeout 10; the Bash entry is
  unchanged.
- A2 every row R1–R8 has a test with the exact exit code and, for blocks, the exact stderr line.
- A3 escape cases blocked: `../` out of `research/`, sibling prefix `research-evil/`,
  symlinked dir inside `research/` pointing outside, relative `file_path`, `build-state.yaml`
  in a subdir, symlink → `build-state.yaml`.
- A4 role parsing: `bytedigger:explorer`, `explorer`, `architect`, `synthesizer` restricted;
  `general-purpose`, `bytedigger:foo`, missing `agent_type` with `agent_id` present → only R5.
- A5 `scratchpad_dir` with spaces and quotes (`"…"`) works; relative `scratchpad_dir` resolves
  against `cwd`.
- A6 `current_phase: completed` or no state file → allow everything (incl. state file).
- A7 no python3 on PATH → exit 0 + the WARN line.
- A8 docs: `docs/plugin.md` hooks table row; CHANGELOG entry; CI `manifests` job runs the new
  test file.

## §6 Tests

`tests/test_worker_write_guard.py` (pytest, stdlib only, runs the hook as a subprocess with
JSON on stdin in a tmp dir), added to CI `manifests`.
