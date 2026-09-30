# bd#133 — PreToolUse path guard for subagent Write/Edit

**Status: FROZEN Rev 2.1** (gate r1 REJECT → F1–F6 + minors; gate r2 PASS; r2 advisories n1–n3 folded in) · **Class:** SYSTEMATIC · **Chokepoint:** one new PreToolUse hook,
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
(stated as a limit in `docs/security.md`, A8; not fixed here).

## §3 Inputs the hook reads

- stdin JSON (Claude Code PreToolUse): `tool_name`, `tool_input.file_path`
  (`tool_input.notebook_path` for NotebookEdit), `cwd`, and — only when the call comes from a
  subagent — `agent_id` and `agent_type`.
- `<cwd>/build-state.yaml`: `current_phase`, `scratchpad_dir`. `cwd` from stdin; if absent,
  the process CWD. Only `<cwd>/build-state.yaml` is read — the same assumption
  `build-gate.sh` makes (the orchestrator's CWD is the build root; after a Phase-0 worktree
  move, the worktree).

**State parsing (F3).** For key `k`: the first line matching `^k:` (anchored at column 0);
value = text after the first `:`, with surrounding whitespace and any `\r` stripped, then one
pair of matching surrounding quotes (`"…"` or `'…'`) stripped. Missing key → empty.

**No interpolation (F4).** `file_path`, `cwd`, `agent_type` and state values reach Python only
through stdin, argv or environment — never interpolated into program text.

Definitions:
- **Build active** ⇔ `<cwd>/build-state.yaml` exists and `current_phase` is non-empty and not
  `completed`. (Phase 7 and `awaiting_approval` are active.)
- **Subagent call** ⇔ stdin has a non-empty string `agent_id`. Main-thread calls (the
  orchestrator) have none and are never blocked by R5–R7.
- **Role (m3)** = `agent_type` if it has no `:`; the part after `bytedigger:` if it starts with
  `bytedigger:`; otherwise (another plugin's `x:explorer`) the full string. **Read-only
  roles** = `explorer`, `architect`, `synthesizer` — so bare names (agents copied into
  `.claude/agents/`) and `bytedigger:` names are restricted; `x:explorer` is not.
- **Target** = the path string made absolute against `cwd`, then `os.path.realpath` (symlinks
  and `..` resolved; a not-yet-existing leaf resolves through its existing parents).
- **Scratchpad** = `realpath(scratchpad_dir made absolute against cwd)` (F6; a trailing `/` is
  harmless). **Allowed dirs** = `realpath(<scratchpad>/research)`, `…/architecture`,
  `…/reviews`; an allowed dir whose realpath is not strictly under Scratchpad is dropped
  (m12: a symlinked `research -> /project` grants nothing).
- **Inside (m6)**: target is inside dir D ⇔ target starts with `D + "/"`. target == D is
  outside; `research-evil/x` is outside `research`.
- **Protected name (F2, m1)**: target is protected if the `casefold()` of the raw path's
  basename or of the resolved basename equals `build-state.yaml` or `build-metadata.json`, or
  the target exists and is `os.path.samefile` with `<cwd>/build-state.yaml` or
  `<cwd>/build-metadata.json` (hardlink). The message names the canonical lowercase file
  name.

## §4 Decision table (first matching row wins)

| # | Condition | Result |
|---|---|---|
| R1 | stdin is valid JSON (an object) and `tool_name` is a non-empty string not in {Write, Edit, MultiEdit, NotebookEdit} (F1) | allow (exit 0) |
| R2 | build not active | allow |
| R3 | stdin not a JSON object; or `tool_name` missing/empty/non-string; or `tool_input` not an object; or the path key missing, `null`, non-string or empty (m8) | **block** (build active; fail closed) |
| R4 | not a subagent call | allow |
| R5 | target is protected (§3) | **block** |
| R6 | role is read-only and `scratchpad_dir` is empty | **block** |
| R7 | role is read-only and target is not inside an allowed dir | **block** |
| R8 | otherwise | allow |

Block = exit 2, one line on stderr (Claude Code feeds stderr back to the agent) and the same
line on stdout, prefix `BLOCKED (bytedigger write guard): `. Exact reasons:
- R3: `BLOCKED (bytedigger write guard): unreadable tool input during an active build`
- R5: `BLOCKED (bytedigger write guard): subagents may not write <name>; it is orchestrator state` (`<name>` = `build-state.yaml` or `build-metadata.json`)
- R6: `BLOCKED (bytedigger write guard): <role> may write only its scratchpad deliverable, but build-state.yaml has no scratchpad_dir`
- R7: `BLOCKED (bytedigger write guard): <role> may write only under <scratchpad>/{research,architecture,reviews}/, not <target>` (`<scratchpad>`, `<target>` = the realpaths of §3)

**Crash policy (n2).** Any unexpected error inside the check while a build is active (e.g.
`ValueError` from a NUL byte in the path, `OSError` from `samefile`) → the R3 block (exit 2),
never exit 1 (the host treats exit 1 as non-blocking).

**R5 name (n3).** When the raw name and the resolved/samefile file differ, `<name>` is the
resolved/samefile file.

In every message, a `\n` or `\r` character inside an interpolated value is written as the two
characters `\n` / `\r` (m11), so the reason stays one line.

python3 absent → allow with one stderr line
`WARN (bytedigger write guard): python3 not found; write guard disabled` (same degrade policy
as `build-state-guard.sh`; `docs/plugin.md` already lists python3 as optional for the guard).

**Known limits** (documented in `docs/security.md`, not fixed): Bash writes by general
workers; an orchestrator `cd` into a subdir that persists (no state file in cwd → guard off);
a stale `build-state.yaml` left in the main checkout after a worktree build keeps the guard on
there; NTFS alias names (trailing dot/space, 8.3 short names); Claude Code < 2.1.69 (no
`agent_id` → every call looks like main thread).

## §5 Acceptance

- A1 `hooks.json` has a PreToolUse entry, matcher exactly `Write|Edit|MultiEdit|NotebookEdit`,
  command `${CLAUDE_PLUGIN_ROOT}/hooks/worker-write-guard.sh`, timeout 10; the Bash entry is
  unchanged. The hook file is executable and starts with a bash shebang; it works when run
  directly (no `bash` prefix), as the host runs it (F5).
- A2 every row R1–R8 has a test with the exact exit code and, for blocks, the exact stderr line;
  valid JSON without `tool_name` during a build → R3 (F1).
- A3 escape cases blocked: `../` out of `research/`, sibling prefix `research-evil/`,
  symlinked dir inside `research/` pointing outside, relative `file_path`, `build-state.yaml`
  in a subdir, symlink → `build-state.yaml`, case variants `Build-State.yaml` /
  `BUILD-METADATA.JSON` (F2), hardlink to `build-state.yaml` (m1), `research` itself a symlink
  to the project root (m12), path with shell metacharacters `'"$(…)` and backticks — blocked by
  R7 and no command runs (F4).
- A4 role parsing: `bytedigger:explorer`, `explorer`, `architect`, `synthesizer` restricted;
  `general-purpose`, `bytedigger:foo`, `other:explorer`, missing `agent_type` with `agent_id`
  present → only R5.
- A5 `scratchpad_dir` with spaces, double quotes, single quotes, `'`/`$` inside the value, a
  trailing `/`, relative (resolves against `cwd`); scratchpad and target reached through a
  symlinked alias of the project in either direction → allowed (F6).
- A6 not active: `current_phase: completed` (quoted `"…"`, `'…'`, unquoted) or no state file →
  allow everything (incl. state file). Parsing: a `task:` line containing `current_phase:
  completed` does not switch the guard off; a CRLF state file parses (F3).
- A7 no python3 on PATH → exit 0 + the WARN line.
- A8 docs: `docs/plugin.md` hooks table row; `docs/security.md` names the hook and every §4 known limit
  (m5, n1); CHANGELOG entry; CI `manifests` job runs the new test file.
- A10 NUL byte in `file_path` during an active build → R3 line, exit 2 (n2).
- A9 newline in a target path → the R7 reason is still one line, with `\n` written out (m11).

## §6 Tests

`tests/test_worker_write_guard.py` (pytest, stdlib only, runs the hook as a subprocess with
JSON on stdin in a tmp dir), added to CI `manifests`.
