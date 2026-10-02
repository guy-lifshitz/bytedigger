# bd#136 — write guard follow-ups: subagent Bash writes, stale main-checkout state, atomic state rewrites

**Status: FROZEN Rev 2** (gate r1 REJECT → M1–M4 + minors, see §R2) · **Class:** SYSTEMATIC · **Builds on:** bd#133 (PR #137,
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

## §R2 Rev 2: gate r1 (REJECT, M1–M4) folded in

Where this section and §3–§6 disagree, this section wins.

- **M1, R1 changes meaning.** bd#133 R1 becomes "a named tool outside `WRITE_TOOLS ∪ {Bash}`
  → allow". RED rev 2 updates `tests/test_worker_write_guard.py::test_r1_non_write_tool_allowed`
  (drop the `Bash` case; B2 covers it).
- **M2, a second copy instruction.** `commands/build.md` ("**Worktree:**" line) says `cp build-state.yaml`.
  It moves too (`mv build-state.yaml` and `build-metadata.json`). A7 pins both files: no
  `cp build-state.yaml` / `cp build-metadata.json` in `phases/*.md` or `commands/build.md`.
- **M3, glob false positives.** The §4 glob rule applies only to a token that contains `build`
  or `bytedigger` (casefolded) as literal text. `pytest tests/* | tee build-red-output.log`,
  `rm -rf dist/*`, `--include=*.json`, `find -name '*.log'` are allowed; `build-*.yaml`,
  `build-stat?.yaml`, `.bytedigger-*` are blocked. Bare `*.yaml` / `*.json` are allowed and
  listed as a limit (A8). A4 pins these.
- **M4, `/build continue` from the main checkout.** Resume Check step 3 (`phase-0-classify.md`)
  and the `commands/build.md` "Resumable" text: before "No build state found", look for
  `.bytedigger/worktrees/*/build-state.yaml` with `current_phase` != `completed`. For each one, print
  `Build state is in worktree <path>: cd there and run /build continue` and stop. A7 pins the glob
  and the message in both files.
- **m1.** The A7 truncation pin forbids `write_text(` / `open(...,'w')` only when the target is
  the state file itself (`build-state.yaml` not followed by `.tmp`). `…/build-state.yaml.tmp`
  + `os.replace` is the required form. The phase-0 initial creation converts too (harmless, uniform).
- **m5.** B1 is evaluated before B2: a main-thread Bash call with a malformed `command` is allowed
  (the same degrade as R4). This is pinned.
- **m6/m7, docs.** One wording: "hook-less backend". `docs/security.md` keeps "The synthesizer has
  no Bash" (the role confinement still depends on it). The "Bash writes" bullet is replaced by the
  Bash rule; the section intro and the `.sh` header name `Bash`. The #189 engine_py / `claude -p`
  bullet is reused, not duplicated (A8 reads its phrases from there).
- **A8 new limit bullets:** the name match is not a sandbox (computed names, brace expansion
  `{build-state,x}.yaml`, a script file that writes the state, bare `*.yaml` / `*.json` globs);
  the tee exemption checks shape, not content, so a subagent can still write a fake
  `build-red-output.log` with `echo … | tee`; B0 catches an empty file only, and a partially written
  file without `current_phase` still reads as "no build"; the guard assumes the hook's cwd is the
  build's checkout (bd#133 §3); after `mv` the main checkout holds no state and is unguarded,
  which is correct because the build is no longer there. `build-state.yaml.tmp` is not protected
  (exists for microseconds, orchestrator-only).
- **Extra A2/A3 fixtures:** `… | tee build-red-output.log build-state.yaml` blocked; a command
  with a newline joining a tee line and `sed -i x build-state.yaml` blocked;
  `pytest tests/* 2>&1 | tee build-red-output.log` allowed.
- Out of scope, noted: the stale `.bytedigger-orchestrator-pid` in the main checkout (no hook reads
  it; see #190).

### §R2.1 gate r2 advisories (PASS-WITH-ADVISORIES)

- **A6 shape:** `worker-write-guard.sh` is added as the **second hook of the existing `Bash`
  entry** in `hooks/hooks.json` (after `build-state-guard.sh`); no new entry, and the Write matcher is unchanged.
- **Docs:** the security.md guard section keeps the word "worktree" (the stale-state/`mv` text).
  The sentence "it is blocked only when the tool input is malformed" gains "(file tools)": a
  malformed **main-thread** Bash call is allowed (m5), unlike the file tools (R3 before R4).
- **Stale text above:** §4's `*.yaml` example, §5's "`/build continue` runs from the worktree"
  and §6 A8's "API-token engine path" wording are superseded by §R2 (do not copy them).
- **Glob limit wording:** "globs without a literal `build`/`bytedigger`" (e.g. `*.yaml`,
  `b*-state.yaml`, `[b]uild-state.yaml`) are not caught. Known false positives: a subagent grepping
  for `build-state.yaml` (e.g. in this repo) and `pytest -k 'build*' | tee …` are blocked.
- Recorded as known MINORs, not fixed: the test docstring lists two pins dropped in rev 2; the
  m1 per-line pin excuses any line that names `build-state.yaml.tmp`.
