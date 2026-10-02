> This is Phase 0 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 0: CLASSIFY + INIT

**First ACTION — Render the host companion**, before `build-state.yaml` or `build-metadata.json` is written. This file is opened with Read, so `${CLAUDE_PLUGIN_ROOT}` below is not substituted: use the absolute plugin root the loaded skill shows in its place.
```bash
BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger
```
- Exit 0: keep following the loaded skill; apply each `bd:local begin/end` block in stdout as an addition to the section it names (no block = no companion).
- Exit 3: STOP. Report stderr to the user and register nothing (no `build-state.yaml`, no `build-metadata.json`).
- Any other exit: use the core skill as loaded and print one line: `W_SKILL_COMPANION_UNAVAILABLE exit=<n> — host companion NOT applied`.

On `/build continue`, run it again before `build-state.yaml` is read.

**Next ACTION — Create build-state.yaml:**
```bash
python3 -c "
import datetime
yaml='''task: \"TASK_DESCRIPTION\"
complexity: PENDING
mode: AUTONOMOUS
current_phase: \"0\"
completed_phases: []
iteration_count: 0
files_modified: []
forge_run_id: \"forge-'$(date +%s)'\"
started_at: \"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'\"
last_updated: \"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'\"
spec_path: \"./build-spec.md\"
test_spec_path: \"./build-tests.md\"
'''
open('build-state.yaml','w').write(yaml)
print('build-state.yaml created')
"
```
Replace TASK_DESCRIPTION with the actual task. Update `complexity` after classification.

**Immutable Metadata (MANDATORY):** After classification, create build-metadata.json — this file MUST NOT be modified after Phase 0:
```bash
python3 -c "
import json, datetime
metadata = {
    'complexity': '\$COMPLEXITY',
    'mode': '\$MODE', 
    'created_at': datetime.datetime.utcnow().isoformat() + 'Z',
    'task_hash': '\$TASK_HASH'
}
with open('build-metadata.json', 'w') as f:
    json.dump(metadata, f, indent=2)
print('build-metadata.json created')
"
```
hooks/build-gate.sh uses this file to detect complexity downgrade attempts. If build-state.yaml says SIMPLE but metadata says FEATURE, the gate blocks.

**Create scratchpad directory** (shared workspace for cross-phase findings):
```bash
SCRATCHPAD="$(pwd)/.bytedigger"  # ABSOLUTE path — survives worktree CWD switches
mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}
echo "scratchpad_dir: \"$SCRATCHPAD\"" >> build-state.yaml
echo "Scratchpad created: $SCRATCHPAD"
```

`.bytedigger/` lives in project CWD — persists with worktree and survives reboots. Add to `.gitignore` if not already present. **Always store absolute path** in build-state.yaml so agents in any CWD (including worktrees) can locate it.

**Inject prior learnings** (after scratchpad creation, before Phase 4.5):
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
KEYWORDS=$(grep '^task:' build-state.yaml | sed 's/^task:[[:space:]]*//; s/^"//; s/"$//' | tr ' ' '\n' | awk 'length>3' | tr '\n' ' ')
[ -z "$KEYWORDS" ] && KEYWORDS="build"
bash scripts/learning-store.sh inject "$KEYWORDS" > "${SCRATCHPAD}/research/prior-learnings.md" 2>/dev/null || true
# Remove empty prior-learnings.md (no learnings found)
[ -s "${SCRATCHPAD}/research/prior-learnings.md" ] || rm -f "${SCRATCHPAD}/research/prior-learnings.md"
```
If `learning.backend` is `none`, this exits immediately and writes no files. Later agents will find `research/prior-learnings.md` if learnings exist.

**Arm orchestrator guard** (tool enforcement — blocks orchestrator from editing code files):
```bash
touch .bytedigger-orchestrator-pid
```
This file arms the orchestrator guard hook. Agent detection uses env vars (`CLAUDE_AGENT_ID`, `SIDECHAIN`, etc.) — agents are allowed, orchestrator is blocked. Cleanup: Phase 7 deletes this file alongside build-state.yaml.

Workers write findings to scratchpad files instead of returning only in chat. This enables:
- Phase 4.5 → Phase 5: implementer reads `specs/build-spec.md`
- Phase 6: reviewers read `reviews/` for dedup

Every tier runs the same flow: 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7. The tier only changes the knobs (spec length, reviewer count, timeouts).

You are the orchestrator performing initial classification and pipeline setup for a /build run.

## What You Receive

- The user's feature request / task description
- CWD (current working directory)
- Any flags: `--dry-run`, `--worktree`, `--pr`, `--supervised`, `--auto`, `--init`, `--atomic-commits`, `--issue <N>`

## What You Must Produce

1. Complexity classification (TRIVIAL / SIMPLE / FEATURE / COMPLEX)
2. Mode determination (AUTONOMOUS / SUPERVISED)
3. Project context (language, manifests, test/build commands, constitution)
4. Model allocation lookup (from templates/dynamic-context.md)
5. Todo list with all pipeline phases
6. `build-state.yaml` initialized in project CWD

## Resume Check

If `/build continue` was invoked:
1. Read `build-state.yaml` from CWD
2. If found and `current_phase` != "completed":
   - Display: `Resuming: [task] from Phase [current_phase]`
   - Display: `Completed: [completed_phases]`
   - Skip to the phase AFTER the last completed phase
   - Continue pipeline normally from there
3. If not found: `No build state found. Start a new /build.`
4. If found but `current_phase` == "completed": `Previous build completed. Start a new /build.`
5. If found and `current_phase` == "awaiting_approval": `awaiting_stage: start` → re-run the start gate below (0 → continue with the next phase); `awaiting_stage: ship` → re-run only SHIP (Phase 7, `scripts/ship.sh --pr`), never re-implement.

## Issue Intake and Readiness (bd#117)

Runs after `skill-companion render` when that step exists, and before `build-state.yaml` is written (every tier, TRIVIAL included).

1. Parse `--issue <N>` from the flags.
2. Run `bash scripts/readiness check --stage start --json` only to read `required` (the `readiness.required` field of its JSON output). Exit 3 is expected here (no spec record yet, or the build is not yet on its `gh<N>-` branch): it is not a stop — the JSON is still printed, so read `readiness.required` from it and continue. Exit 4 → print the `W_READINESS_UNAVAILABLE` line as a warning and continue; it is not a stop.
3. `readiness.required` is true and no issue is bound (no `--issue <N>`, and the branch does not parse as `gh<N>-...` / `batch/<N>`) → STOP with `E_READINESS_NOT_APPROVED no_issue #` before `build-state.yaml` is written.
4. `--issue <N>` and the current branch parses to another number → STOP with `issue_mismatch` (a Phase 0 message only, not a verdict reason).
5. Otherwise put the build on branch `gh<N>-<slug>` for every tier, with or without a worktree.

### Start gate (only when `readiness.required` is true)

Runs before the first write outside the scratchpad:

- **TRIVIAL**: write a minimal `build-spec.md` (`Task | Files | Change`) first, run the gate, then make the direct edit.
- **SIMPLE / FEATURE / COMPLEX**: after Phase 4.5 (the same spec path for every tier).

Run `bash scripts/readiness check --stage start --spec ./build-spec.md`:

- **0** → continue.
- **3** → run `bash scripts/readiness post --spec ./build-spec.md`, set `current_phase: awaiting_approval` and `awaiting_stage: start`, print `Waiting for "<label>" on #<N>`, and STOP — in every mode, AUTONOMOUS included (the one named exception to the no-pause rule, keyed on `readiness.required`, not on mode).
- **anything else** → warn with the stderr line and continue (the ship gate still holds).

Every readiness STOP message prints the verdict line and, for `no_spec_record` / `label_predates_spec`, the recovery: `scripts/readiness post --spec ./build-spec.md`, then a human adds `<label>`, then `/build continue`.

## Complexity Classification

- **TRIVIAL**: docs/config edit <10 lines → write `build-state.yaml` with `complexity: TRIVIAL` and `mode: AUTONOMOUS`, do direct edit, then proceed directly to Phase 7 (skips Phases 4.5–6; Phase 7 gate bypasses `review_complete` and `phase_5_implement` checks for TRIVIAL)
- **SIMPLE**: bug fix ONLY — fixing broken behavior, 1-3 files, clear root cause, NO new functionality. Examples: null pointer fix, typo fix, broken import, test fix. If the task adds ANY new behavior or capability → it is NOT SIMPLE.
- **FEATURE**: adds new functionality OR changes existing behavior, any file count, clear spec -> AUTONOMOUS mode, full pipeline. Examples: new endpoint, new UI component, new skill, refactoring a module, adding a config option. **DEFAULT for ambiguous cases** — when in doubt, classify as FEATURE, not SIMPLE.
- **COMPLEX**: 4+ files, architecture/refactor/design, ambiguous scope, cross-cutting concerns -> SUPERVISED mode, full pipeline

**Classification guard:** If the user says "feature", "add", "create", "implement", "build" → NEVER classify as SIMPLE. SIMPLE is reserved strictly for bug fixes with clear root cause.

## Mode Determination

Default: determined by complexity — SIMPLE/FEATURE → AUTONOMOUS, COMPLEX → SUPERVISED. Override with --supervised or --auto flags.

- **SUPERVISED**: `--supervised` flag, OR COMPLEX classification, OR ambiguous scope
- **AUTONOMOUS**: `--auto` flag, OR SIMPLE/FEATURE classification

## Project Context Check

1. Detect project structure. Check ALL of these:
   - **Primary manifests**: package.json, pyproject.toml, Package.swift, pom.xml, Cargo.toml, go.mod, build.gradle
   - **Secondary indicators**: requirements.txt, setup.py, setup.cfg, Makefile, CMakeLists.txt, Gemfile, composer.json, .csproj
   - **Config files**: tsconfig.json, jest.config.*, pytest.ini, tox.ini, .eslintrc, CLAUDE.md
   - **Build pipeline**: constitution.md, .claude/constitution.md -- project rules for /build agents
   - If ANY found: note language, dependencies, test/build/lint commands
   - If constitution.md NOT found: recommend `--init` to create one
   - If NO manifest at all: warn user, recommend creating one. SUPERVISED: ask before proceeding. AUTONOMOUS: create minimal manifest for detected language.
2. If CWD is unclear: ask user which project this is for.

### Dependency Pre-Check

After manifest detection, validate dependency health (MUST complete before Phase 4.5):

1. **Lock file presence** — If manifest found, check for corresponding lock file:
   - package.json → package-lock.json OR yarn.lock OR pnpm-lock.yaml
   - pyproject.toml/requirements.txt → (no standard lock, skip)
   - Cargo.toml → Cargo.lock
   - go.mod → go.sum
   - If lock file missing: write `deps_lock_missing: true` to build-state.yaml, log warning

2. **Quick validation** (run in background, max 30s timeout):
   - npm/yarn/pnpm: `npm ls --depth=0 2>&1 | tail -5` (check for UNMET PEER/missing)
   - cargo: `cargo check 2>&1 | tail -5` (syntax + dependency resolution)
   - go: `go mod verify 2>&1 | tail -3`
   - pip: skip (no reliable dry-run)
   - If command fails or times out: log warning, do NOT block pipeline

3. **Write to build-state.yaml:**
   - `deps_checked: true`
   - `deps_issues: "<summary>"` (only if issues found, one line)
   - `deps_lock_missing: true` (only if lock file missing)

**Behavior:** This is a SOFT CHECK — warns but never blocks. Phase 5 workers see the warning and can handle it.

## Actions Summary

1. Project context check
2. Classify complexity (TRIVIAL / SIMPLE / FEATURE / COMPLEX)
3. If TRIVIAL: write `build-state.yaml` fields (`complexity: TRIVIAL`, `mode: AUTONOMOUS`), do the direct edit, then skip ahead to Phase 7 (do NOT run Phases 4.5–6)
4. Create todo list with phases (all tiers run 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7)
5. Determine and display mode: `Mode: [AUTONOMOUS|SUPERVISED] -- [reason] | Complexity: [level]`
6. Look up model allocation from the Model Allocation table based on complexity level
7. Display: `Project: [manifest] | Language: [lang] | Test cmd: [cmd] | Build cmd: [cmd] | Models: [allocation]`
8. If `--init` flag: write constitution template to `./constitution.md`, stop

## --dry-run Early Exit

If `--dry-run` flag is set, display the following and STOP (do not proceed to Phase 0.5):

| Aspect | Value |
|--------|-------|
| **Task** | [feature request text] |
| **Complexity** | [SIMPLE/FEATURE/COMPLEX] |
| **Mode** | [AUTONOMOUS/SUPERVISED] |
| **Phases** | [list phases that will run] |
| **Model allocation** | [table from model allocation section] |
| **Estimated agents** | [count of Task agents that will spawn] |
| **Review agents** | [3 for SIMPLE, 6 for FEATURE/COMPLEX] |
| **Opus gates** | Test validation (Phase 5.2) + Satisfaction scoring (Phase 6) |
| **Constitution** | [found/not found] |

Then STOP.

## --worktree Isolation

If `--worktree` flag is set OR complexity is COMPLEX with `--pr` flag:

1. Create git worktree: `git worktree add .bytedigger/worktrees/build-[slug]-[timestamp] -b build/[slug]` (branch `gh<N>-[slug]` instead when an issue is bound via `--issue <N>`)
2. **Copy state files into worktree** — without this, ALL gates are blind and pipeline runs unprotected:
   ```bash
   WT=<worktree-path>
   cp build-state.yaml "$WT/"
   [ -f build-metadata.json ] && cp build-metadata.json "$WT/"
   ```
3. **Re-anchor scratchpad inside worktree** — the `.bytedigger/` created in main checkout is unreachable from worktree CWD. Recreate inside worktree and rewrite `scratchpad_dir` to its absolute path:
   ```bash
   NEW_SCRATCH="$(cd "$WT" && pwd)/.bytedigger"
   mkdir -p "$NEW_SCRATCH"/{research,architecture,specs,tests,reviews}
   python3 -c "import re,pathlib;p=pathlib.Path('$WT/build-state.yaml');t=p.read_text();t=re.sub(r'scratchpad_dir:.*', f'scratchpad_dir: \"$NEW_SCRATCH\"', t);p.write_text(t)"
   ```
4. **Re-arm tool guard after CWD switch** — the `.bytedigger-orchestrator-pid` from the main checkout is unreachable from the worktree. Touch it inside the worktree so the PreToolUse hook stays armed:
   ```bash
   touch "$WT/.bytedigger-orchestrator-pid"
   ```
5. All subsequent phases run in the worktree CWD, not the original. Switch CWD before proceeding.
6. **Record ABSOLUTE worktree path** in `build-state.yaml` (in worktree). Relative paths break later cleanup which may run from any CWD:
   ```bash
   WT_ABS="$(cd "$WT" && pwd)"
   echo "worktree_path: \"$WT_ABS\"" >> "$WT/build-state.yaml"
   ```

**Cleanup:**
- On successful SHIP (PR created): worktree persists until PR merged, then `git worktree remove [path]`
- On pipeline failure: worktree persists for inspection
- Stale worktrees (>7 days, no uncommitted changes): safe to remove

## Resumable State Schema

```yaml
task: "feature description"
complexity: SIMPLE | FEATURE | COMPLEX
mode: AUTONOMOUS | SUPERVISED
current_phase: "0"
completed_phases: []
iteration_count: 0
files_modified: []
forge_run_id: "forge-{timestamp}"
started_at: "ISO-8601"
last_updated: "ISO-8601"
spec_path: "./build-spec.md"
test_spec_path: "./build-tests.md"
```

At EVERY phase transition: update `current_phase`, append to `completed_phases`, update `last_updated`, update `files_modified`.

## Model Allocation Reference

| Phase | SIMPLE | FEATURE | COMPLEX |
|-------|--------|---------|---------|
| 4.5 Spec | Sonnet | Sonnet | Sonnet |
| 5.1 Red | Haiku | Sonnet | Opus |
| 5.2 Validate | Opus | Opus | Opus |
| 5.3 Green | Sonnet | Sonnet | Sonnet |
| 6 Review | 3x reviewers | 6x reviewers | 6x reviewers |
| 6 Satisfaction | Opus (3 dim) | Opus (5 dim) | 3x Opus voting (5 dim) |
| 7 Synthesize | Haiku | Haiku | Haiku |

Models are configurable via `bytedigger.json`.

## Agent Status Protocol

ALL Task agents MUST return a status footer as their LAST output:

```
---
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
CONCERNS: [list concerns, only if DONE_WITH_CONCERNS]
BLOCKED_ON: [description, only if BLOCKED]
CONTEXT_NEEDED: [what's missing, only if NEEDS_CONTEXT]
---
```

| Status | AUTONOMOUS mode | SUPERVISED mode |
|--------|----------------|-----------------|
| DONE | Proceed to next phase | Proceed to next phase |
| DONE_WITH_CONCERNS | Log concerns, proceed | Show concerns to user, ask to proceed or address |
| NEEDS_CONTEXT | Provide missing context, re-run (max 2 retries) | Ask user for context, re-run |
| BLOCKED | STOP pipeline | Show blocker to user, ask for resolution |

## Exit Criteria

- [ ] Complexity classified
- [ ] Mode determined
- [ ] Project context detected (manifests, language, commands)
- [ ] Todo list created with all phases
- [ ] `build-state.yaml` written
- [ ] Dependency pre-check run, `deps_checked` written to build-state.yaml
