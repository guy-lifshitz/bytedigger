# Injection Blocks

> Definitions of the two blocks that `phases/phase-5-implement.md` makes MANDATORY in every Phase 5 worker prompt.
> Assembly order: `$CONSTITUTION_BLOCK`, then `$QUALITY_GATE_BLOCK`, then the worker's purpose statement, then the phase instructions.
> Used for the RED worker, the Gherkin writer, the Opus validator and the GREEN worker.

## $CONSTITUTION_BLOCK

The project's rules: the file named by `constitution_path` in `bytedigger.json` (default `./constitution.md`), as the engine renders it to `injection/constitution.md`. For a plain `.md` constitution that is the file's contents unchanged; a SpecKit `.json` constitution is rendered to markdown and layered constitutions are merged by the engine. Do not summarize or reorder it.

- The engine's Phase 0.5 writes it to `injection/constitution.md` in the scratchpad (the engine may resolve a layered or checklist constitution; its output file is the block).
- With no orchestrator-side engine, read the file at `constitution_path` yourself and paste its contents.
- When no constitution is found, the block is the engine's sentinel: the heading `## Constitution` followed by the line `No project constitution found.` Inject that sentinel; do not invent project rules.

## $QUALITY_GATE_BLOCK

Two parts, in this order. Part one is written by the engine; part two is added by the orchestrator, the engine does not write it.

Part one: the full contents of the engine's `injection/quality-gate.md` (Phase 0.5; the text is `QUALITY_GATE_TEXT` in `engine_py/bytedigger_engine/workflows/phase_05_inject.py`, and with no engine that is the text to paste). It also carries the evaluator anti-fabrication rules and the output rules. Its core, the "Quality Gate — Zero Corner-Cutting" rules, in summary:

- ALL issues found MUST be fixed, not just high severity. "Acceptable", "pre-existing issue", "will fix in follow-up", "low severity / cosmetic" and "acceptable for MVP" are not valid reasons to skip a fix.
- If something truly cannot be fixed, report BLOCKED with a clear explanation. Never silently pass it.
- Boy Scout Rule: leave every file you touch CLEANER than you found it. No exceptions.

Part two: the TEST INTEGRITY RULE (added by the orchestrator to every worker prompt that touches tests):

> Tests verify REAL system behavior against the spec. If a test fails, the CODE is wrong — not the test. NEVER modify test assertions to match broken behavior. NEVER weaken a test to make it pass. If a test checked for a fallback and the fallback was removed, the test should now verify that WITHOUT the fallback, the expected error/empty-state occurs — not silently pass. The only valid reason to change a test is if the SPEC changed. "Make tests green" means "fix the code so tests pass", not "change assertions so they match whatever the code does."

Part two is a byte-for-byte copy of the "TEST INTEGRITY RULE" in `phases/phase-5-implement.md`, which is authoritative; keep the copy in step with it and inject it verbatim.
