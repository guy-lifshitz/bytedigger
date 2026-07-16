# Claude Code skill

Run ByteDigger as a `/build` skill inside Claude Code.

## Option 1: plugin (recommended)

```bash
claude plugin add shtofadhor/bytedigger
```

Installs the skill, commands, per-phase agent instructions, and gate hooks in one step. Nothing else to configure -- skip the rest of this page.

## Option 2: manual drop-in

For a single project, or when you can't install plugins:

```bash
# 1. Clone the pipeline somewhere stable
git clone https://github.com/shtofadhor/bytedigger ~/tools/bytedigger

# 2. Drop the skill into your project (or ~/.claude/skills/ for all projects)
mkdir -p .claude/skills/bytedigger
cp ~/tools/bytedigger/examples/claude-code-skill/SKILL.md .claude/skills/bytedigger/

# 3. Point the skill at your checkout
#    Edit BYTEDIGGER_HOME inside the copied SKILL.md if you cloned elsewhere.

# 4. Project config (models, gates, thresholds)
cp ~/tools/bytedigger/bytedigger.json ./bytedigger.json
```

Restart the Claude Code session (skills load at session start), then:

```
/build "add email verification"
```

## What the skill does

`/build` classifies the task (TRIVIAL/SIMPLE/FEATURE/COMPLEX), routes it through the phase pipeline, and enforces TDD in Phase 5: frozen spec → failing RED tests → independent gate audit → GREEN implementation. Gates sit between phases -- agents can't skip steps or review their own work.

`bytedigger.json` controls model allocation (`validation_model`, `agent_model`, `exploration_model`), reviewer counts, satisfaction thresholds, and which gates are enabled. The shipped defaults are sane; tune per project.

## Notes

- The manual drop-in copies only SKILL.md -- the pipeline files (`commands/`, `phases/`, `templates/`) stay in the checkout and are read from `BYTEDIGGER_HOME`. Update with `git pull`, no re-copy needed.
- Hook-based gate enforcement (`hooks/hooks.json`) is plugin-only; the manual path still runs the pipeline but relies on the orchestrator following the gate protocol in `commands/build.md`.
