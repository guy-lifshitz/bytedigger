> This is Phase 0 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 0: CLASSIFY + INIT

**First ACTION — Create build-state.yaml:**
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

**Inject prior learnings** (after scratchpad creation, before Phase 1):
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
KEYWORDS=$(grep '^task:' build-state.yaml | sed 's/^task:[[:space:]]*//; s/^"//; s/"$//' | tr ' ' '\n' | awk 'length>3' | tr '\n' ' ')
[ -z "$KEYWORDS" ] && KEYWORDS="build"
bash scripts/learning-store.sh inject "$KEYWORDS" > "${SCRATCHPAD}/research/prior-learnings.md" 2>/dev/null || true
# Remove empty prior-learnings.md (no learnings found)
[ -s "${SCRATCHPAD}/research/prior-learnings.md" ] || rm -f "${SCRATCHPAD}/research/prior-learnings.md"
```
If `learning.backend` is `none`, this exits immediately and writes no files. Agents in Phase 2+ will find `research/prior-learnings.md` if learnings exist.

**Arm orchestrator guard** (tool enforcement — blocks orchestrator from editing code files):
```bash
touch .bytedigger-orchestrator-pid
```
This file arms the orchestrator guard hook. Agent detection uses env vars (`CLAUDE_AGENT_ID`, `SIDECHAIN`, etc.) — agents are allowed, orchestrator is blocked. Cleanup: Phase 7 deletes this file alongside build-state.yaml.

Workers write findings to scratchpad files instead of returning only in chat. This enables:
- Phase 2 → Phase 4: architect reads `research/` findings directly
- Phase 4 → Phase 5: implementer reads `architecture/` decisions
- Phase 6: reviewers read `reviews/` for dedup

You are the orchestrator performing initial classification and pipeline setup for a /build run.

## What You Receive

- The user's feature request / task description
- CWD (current working directory)
- Any flags: `--dry-run`, `--worktree`, `--pr`, `--supervised`, `--auto`, `--init`, `--atomic-commits`

## What You Must Produce

1. Complexity classification (TRIVIAL / SIMPLE / FEATURE / COMPLEX)
2. Mode determination (AUTONOMOUS / SUPERVISED)
3. Project context (language, manifests, test/build commands, constitution)
4. DevOps profile detection (code / devops)
5. Model allocation lookup (from templates/dynamic-context.md)
6. Todo list with all pipeline phases
7. `build-state.yaml` initialized in project CWD

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

## Complexity Classification

- **TRIVIAL**: docs/config edit <10 lines -> direct edit, skip pipeline
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

After manifest detection, validate dependency health (MUST complete before Phase 1):

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

## DevOps Detection

Check if task involves infrastructure-as-code:

**Triggers**: `.tf`, `.tfvars` (Terraform), `Dockerfile` (Docker), `.github/workflows/*.yml` (GH Actions), K8s manifests (`kind:` + `apiVersion:`), `Chart.yaml`/`values.yaml` (Helm), `docker-compose.yml`, CloudFormation (`AWSTemplateFormatVersion`), `nginx.conf`, Ansible playbooks, `Pulumi.yaml`

If ANY DevOps files detected: set `profile=devops`. This activates extra steps in Phase 5 and Phase 6.

Display: `Profile: DEVOPS | Type: [Terraform/Docker/K8s/etc.]`

## Actions Summary

1. Project context check
2. DevOps detection
3. Classify complexity (TRIVIAL / SIMPLE / FEATURE / COMPLEX)
4. If TRIVIAL: direct edit, stop
5. Create todo list with phases (SIMPLE skips Phases 2-4)
6. Determine and display mode: `Mode: [AUTONOMOUS|SUPERVISED] -- [reason] | Complexity: [level]`
7. Look up model allocation from the Model Allocation table based on complexity level
8. Display: `Project: [manifest] | Language: [lang] | Test cmd: [cmd] | Build cmd: [cmd] | Profile: [code/devops] | Models: [allocation]`
9. If `--init` flag: write constitution template to `./constitution.md`, stop

## --dry-run Early Exit

If `--dry-run` flag is set, display the following and STOP (do not proceed to Phase 1):

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

1. Create git worktree: `git worktree add .bytedigger/worktrees/build-[slug]-[timestamp] -b build/[slug]`
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
| 1 Spec | Orchestrator | — | — |
| 4.5 Spec | — | Sonnet | Sonnet |
| 2 Explore | -- | Haiku | Sonnet |
| 3 Clarify | -- | Haiku | Sonnet |
| 4 Architect | -- | Opus | Opus |
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
- [ ] DevOps profile set (code or devops)
- [ ] Todo list created with all phases
- [ ] `build-state.yaml` written
- [ ] Dependency pre-check run, `deps_checked` written to build-state.yaml
