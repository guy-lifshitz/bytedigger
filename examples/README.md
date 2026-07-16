# Examples

Two ways to run ByteDigger: as a Claude Code skill, or as a plain Python library.

## Claude Code skill (recommended)

```bash
# Plugin install -- skill, commands, phases, hooks in one step
claude plugin marketplace add guy-lifshitz/bytedigger
claude plugin install bytedigger@bytedigger

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

## Verified-TDD loop on a toy repo

```bash
python3 examples/verified-tdd-run/run_demo.py
```

The gates in action, keyless: a frozen spec with an AC table, a vacuous RED
test rejected by the stub-passability lint (`E_RED_STUB_PASSABLE`), then an
honest RED driven through fail-first verification, GREEN, and a passing
suite -- with the whole history replayed from the append-only event log.
See [verified-tdd-run/](verified-tdd-run/) for what is real engine machinery
versus scripted LLM output.
