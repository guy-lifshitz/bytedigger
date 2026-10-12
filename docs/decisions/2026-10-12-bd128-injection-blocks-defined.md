---
red_tests:
  - tests/test_bd128_injection_blocks_defined.py
red_pins: []
sibling_tests: []
paths:
  - templates/injection-blocks.md
  - phases/phase-5-implement.md
  - CHANGELOG.md
---
# bd#128: `$CONSTITUTION_BLOCK` and `$QUALITY_GATE_BLOCK` are defined in one plugin document

**Status: r1, TIER1, docs only.** Issue #128, item 1 only.
**Class:** the phase docs make two variables MANDATORY in four worker prompts and no plugin file defines them, so every orchestrator invents the content.
**Chokepoint:** one new document, `templates/injection-blocks.md`, that `phases/phase-5-implement.md` points to.
**Side of the seam:** plugin documentation. **Owner:** bd. No code, script, config or engine change.

## Out of scope

- Issue item 2 (reviewer count 7 vs 9): already gone (single composite reviewer, `2026-10-03-bd89-p3b1c-single-reviewer-md-gates.md`). Not touched.
- The engine's `QUALITY_GATE_TEXT` / `phase_05_inject.py`: not edited. The document describes what the engine writes; it does not change it.

## Decision

New `templates/injection-blocks.md` with two sections, each headed by the variable name:

1. `## $CONSTITUTION_BLOCK`: the contents of the file named by `constitution_path` in `bytedigger.json` (default `./constitution.md`), verbatim. Phase 0.5 of the engine writes it to `injection/constitution.md` in the scratchpad; with no constitution found the block is the one-line sentinel `No project constitution found.`
2. `## $QUALITY_GATE_BLOCK`: the Phase 5 worker rules: Boy Scout Rule, and TEST INTEGRITY (fix the code, never adjust a test assertion to match broken behavior; the only valid reason to change a test is a changed spec). Phase 0.5 writes it to `injection/quality-gate.md`.

Plus one "Assembly" paragraph: order is `$CONSTITUTION_BLOCK`, then `$QUALITY_GATE_BLOCK`, then the purpose statement; used by RED, Gherkin, Opus validator and GREEN.

`phases/phase-5-implement.md`: each of the four lines that mention the variables names `templates/injection-blocks.md` as their definition. `CHANGELOG.md`: one line under `[Unreleased]`.

## Acceptance criteria

| AC | Case | Expectation |
|---|---|---|
| AC1 | `templates/injection-blocks.md` | exists; has a heading line for `$CONSTITUTION_BLOCK` and one for `$QUALITY_GATE_BLOCK`, once each |
| AC2 | scan of `phases commands templates skills scripts` | each variable has exactly one definition heading in the plugin tree (the issue's "was/should be" grep) |
| AC3 | the `$CONSTITUTION_BLOCK` section | names `constitution_path` and `bytedigger.json` |
| AC4 | the `$QUALITY_GATE_BLOCK` section | contains `Boy Scout` and `TEST INTEGRITY` and the rule that code, not test assertions, is fixed |
| AC5 | every line of `phases/phase-5-implement.md` that mentions either variable | also names `templates/injection-blocks.md` |
| AC6 | every `$..._BLOCK` token used in the plugin docs | has a definition heading (no third undefined block can appear) |
| AC7 GUARD | Boy Scout sentence | the same sentence is in the document and in the engine's `QUALITY_GATE_TEXT` (drift guard, green now) |
| AC8 | `CHANGELOG.md` `[Unreleased]` | mentions `bd#128` |

## Rollback

Revert the commit; nothing else reads the document.
