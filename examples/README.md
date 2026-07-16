# Examples

Two ways to run ByteDigger: as a Claude Code skill, or as a plain Python library.

## Claude Code skill (recommended)

```bash
# Plugin install -- skill, commands, phases, hooks in one step
claude plugin add shtofadhor/bytedigger

# Or drop the skill into a single project by hand
cp -r examples/claude-code-skill ~/.claude/skills/bytedigger

# Then, inside Claude Code:
/build "add email verification"
```

See [claude-code-skill/](claude-code-skill/) for the manual-install layout and what each file does.

## Library (no Claude Code)

```bash
cd engine_py && pip install -e .

# Zero-config smoke: run a workflow, inspect the event log
python3 ../examples/library/minimal_run.py

# Register your own LLM backend (keyless stub included)
python3 ../examples/library/custom_backend.py
```

See [library/](library/) for the engine API walkthrough -- `WorkflowEngine`, `WorkflowContext`, the event log, and the backend injection seam.
