# bd#136 — write guard follow-ups: subagent Bash writes, stale main-checkout state, atomic state rewrites

**Status: FROZEN Rev 1** · **Class:** SYSTEMATIC · **Builds on:** bd#133 (PR #137,
`hooks/worker_write_guard.py`, spec `2026-09-30-bd133-worker-write-path-guard.md` Rev 3).
**Chokepoint:** the same PreToolUse hook, now also registered for `Bash`; plus the two
state-file writers in the phase prompts.

## §1 Problem (measured on `6870861`, #137 rebased on main `0460de7`)

1. **Bash writes (P1).** `worker_write_guard.py` returns at R1 for any tool outside
   `WRITE_TOOLS`. tdd-worker, fixers and reviewers have Bash, so a subagent can run
   `sed -i 's/review_complete:.*/review_complete: pass/' build-state.yaml` and forge a gate
   verdict. `hooks/build-state-guard.sh` parses Bash but blocks only `rm`/`unlink`.
2. **Stale main-checkout state.** `phases/phase-0-classify.md` (`--worktree Isolation`, step 2)
   `cp`s `build-state.yaml` / `build-metadata.json` into the worktree and leaves the main copy at
   `current_phase: "0"`. Both guards then treat the main checkout as an active build forever.
3. **Non-atomic rewrites.** Phase prompts rewrite the state with `pathlib.Path.write_text`
   (truncate, then write): `phase-0-classify.md` (worktree step 3), `phase-45-spec.md:8`,
   `phase-5-implement.md:8`, `phase-6-review.md:8`, `phase-7-synthesize.md:8`. In that window the
   guard reads an empty file, sees `current_phase` missing and allows (fail open).

## §2 Scope

In: items 1–3 of #136. Out (stay open in #136): items 4–6 (per-phase `gate_block_counter`
reset, declarative deliverable table, shared learnings parser).

## §3 Decision table (additions to bd#133 R1–R8; R-numbers of bd#133 unchanged)

`PROTECTED` = the five bd#133 names. "Active build" is bd#133 R2, **plus B0** below.

| Row | Condition (build active) | Result |
|---|---|---|
| B0 | `build-state.yaml` exists and is 0 bytes (or whitespace only) | build is **active** (phase unknown); every rule below and R3–R8 apply. A file that exists with text but no `current_phase` stays "no build" (bd#133 R2). |
| B1 | `tool_name == "Bash"`, no `agent_id` (main thread) | allow |
| B2 | `tool_name == "Bash"`, `tool_input.command` not a non-empty string | block, R3 message |
| B3 | Bash by a subagent, command matches the **tee exemption** (§4) and no other protected reference | allow |
| B4 | Bash by a subagent, command **references** a protected name (§4) | block: `BLOCKED (bytedigger write guard): subagents may not touch <name> from Bash; it is orchestrator state` (one line, stderr + stdout, exit 2) |
| B5 | Bash by a subagent, no reference | allow |

Crash policy, message format, no-python3 degrade and "no build → allow" are as in bd#133.
Reads are blocked too (B4 does not distinguish `cat` from `sed -i`): subagents read state with
the Read tool, which this hook does not guard.

## §4 "References" — deterministic, no shell parsing beyond this

Let `c` = the command with `"`, `'`, `\` removed and casefolded. Split `c` into tokens on
whitespace and on `; | & < > ( ) { } = $ ` + "`" + ` ,`. For each token take the part after
the last `/`. The command references `name` if, for some token `t`:
- `t == name`, or
- `t` contains a glob char (`*`, `?`, `[`) and `fnmatch.fnmatchcase(name, t)` is true
  (e.g. `build-*.yaml`, `build-stat?.yaml`, `*.yaml`), or
- `name` occurs in `c` as a substring at all (catches `x=build-state.yaml`-style tokens the
  split missed). The substring rule subsumes the first; the token rule exists for globs.

**Tee exemption (B3):** after removing every match of the regex
`\|\s*tee\s+(-a\s+)?(\./)?build-(red|green)-output\.log\s*$` (anchored at end of `c`, at most
one match), the remaining text references no protected name. So
`pytest -q 2>&1 | tee build-red-output.log` is allowed;
`echo FAIL > build-red-output.log`, `... | tee build-red-output.log; sed -i x build-state.yaml`,
and `tee build-green-output.log < /dev/null` (no pipe) are blocked.

## §5 Items 2 and 3 (prompt + guard)

- **Item 2:** `phase-0-classify.md` worktree step 2 uses `mv` instead of `cp` for
  `build-state.yaml` and `build-metadata.json` (`[ -f … ] && mv …`). Afterwards the main
  checkout has no state file, so neither guard sees a build there. The prompt states it in one
  sentence. `/build continue` runs from the worktree (worktree_path is recorded there).
- **Item 3:** every state rewrite in the phase prompts writes `build-state.yaml.tmp` then
  `os.replace(tmp, 'build-state.yaml')`. No phase prompt may contain
  `write_text(` on the state path or `open('build-state.yaml','w')`. B0 is the guard-side
  backstop for any writer that still truncates.

## §6 Acceptance (tests in `tests/test_worker_write_bash.py`, stdlib + pytest)

- A1 B1–B5 each with exact exit code and message; `bytedigger:`-prefixed and bare agent types.
- A2 forging forms, all blocked: `sed -i`, `echo >`, `>>`, `printf | tee build-state.yaml`,
  `cp x build-metadata.json`, `mv`, `python3 -c "open('build-state.yaml','w')"`, `cat`,
  `./build-state.yaml`, absolute path, `BUILD-STATE.YAML`, `build-*.yaml`, `build-stat?.yaml`,
  `.bytedigger-orchestrator-pid`, quoted `'build-state.yaml'`, `"build-"'state.yaml'`.
- A3 tee exemption: allowed for red and green log, `-a`, `./`; blocked when chained with
  another protected reference, without a pipe, or when not at the end.
- A4 unrelated commands allowed (`pytest`, `cat README.md`, `grep state src/`); no build →
  every command allowed; main thread → every command allowed.
- A5 B0: 0-byte and whitespace-only `build-state.yaml` → subagent Write to `build-state.yaml`
  blocked and subagent Bash `sed -i … build-state.yaml` blocked; text without
  `current_phase` → allowed (bd#133 R2 unchanged).
- A6 `hooks/hooks.json` registers `worker-write-guard.sh` for `Bash` (the existing
  `build-state-guard.sh` stays).
- A7 prompt pins: worktree step uses `mv` for both files and no `cp build-state.yaml`; no
  `write_text(` / `open('build-state.yaml','w')` on the state file in `phases/*.md`; every
  `current_phase →` one-liner uses `os.replace`.
- A8 `docs/security.md`: the "Bash writes" and "Stale state after a worktree build" limits are
  replaced by the new behaviour; a new limit states that the Bash check is a name match, not a
  sandbox (indirection such as computed names or a script file that writes the state is not
  caught), and that the hook runs only on hosts that fire PreToolUse plugin hooks (Claude Code
  and the agent SDK with the plugin loaded); a non-hook backend (API-token engine path without
  hooks) gets no guard and keeps the gate checks of `build-gate.sh` only.
- A9 hook executed directly (subprocess, the real `.sh`), and no-python3 path still allows with
  one WARN line for Bash input too.

## §7 Principles

Deterministic only (no model call, no network). Provider-agnostic: the hook reads the
host's PreToolUse JSON and nothing provider-specific; a host without `agent_id` degrades to
allow (bd#133 §0). Subscription (Claude Code / agent SDK) and API-token runs use the same hook
when the host fires hooks; the limit for hook-less backends is documented (A8).
