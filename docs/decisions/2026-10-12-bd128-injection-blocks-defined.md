---
red_tests:
  - tests/test_bd128_injection_blocks_defined.py
red_pins: []
sibling_tests:
  - engine_py/tests/test_bd89_p1_devops_dropped.py
  - engine_py/tests/test_bd89_p2a_phases_1_4_dropped.py
paths:
  - templates/injection-blocks.md
  - phases/phase-5-implement.md
  - CHANGELOG.md
---
# bd#128: `$CONSTITUTION_BLOCK` and `$QUALITY_GATE_BLOCK` are defined in one plugin document

**Status: r2, docs only; gate r1 REJECTED (F1 MAJOR, F2-F6 MINOR) folded in. Classified TIER1, re-classified MICRO/TIER2 by §1s because a RED test file lands.** Issue #128, item 1 only.
**Class:** the phase docs make two variables MANDATORY in four worker prompts and no plugin file defines them, so every orchestrator invents the content.
**Chokepoint:** one new document, `templates/injection-blocks.md`, that `phases/phase-5-implement.md` points to.
**Side of the seam:** plugin documentation. **Owner:** bd. No code, script, config or engine change.

## Out of scope

- Issue item 2 (reviewer count 7 vs 9): already gone (single composite reviewer, `2026-10-03-bd89-p3b1c-single-reviewer-md-gates.md`). Not touched.
- The engine's `QUALITY_GATE_TEXT` / `phase_05_inject.py`: not edited. The document describes what the engine writes; it does not change it.

## Decision

New `templates/injection-blocks.md` with two sections, each headed by the variable name:

1. `## $CONSTITUTION_BLOCK`: the contents of the file named by `constitution_path` in `bytedigger.json` (default `./constitution.md`), verbatim. Phase 0.5 of the engine writes it to `injection/constitution.md` in the scratchpad; with no constitution found that file is the sentinel `## Constitution` / `No project constitution found.` (two lines, as the engine writes it).
2. `## $QUALITY_GATE_BLOCK`: two parts, stated as such. (a) The engine part: the content of `injection/quality-gate.md`, i.e. the "Quality Gate — Zero Corner-Cutting" rules including the Boy Scout Rule (engine `QUALITY_GATE_TEXT`). (b) The orchestrator part, which the engine does NOT write: the TEST INTEGRITY RULE as it stands in `phases/phase-5-implement.md` (code is fixed, never test assertions; the only valid reason to change a test is a changed spec), appended after (a). The document says plainly that (b) is added by the orchestrator.

Plus one "Assembly" paragraph: order is `$CONSTITUTION_BLOCK`, then `$QUALITY_GATE_BLOCK`, then the purpose statement; used by RED, Gherkin, Opus validator and GREEN.

`phases/phase-5-implement.md`: each of the four lines that mention the variables names `templates/injection-blocks.md` as their definition; on the GREEN spawn line (inside a quoted prompt string) the pointer sits outside the quotes. `CHANGELOG.md`: one line under `[Unreleased]`.

## Acceptance criteria

| AC | Case | Expectation |
|---|---|---|
| AC1 | `templates/injection-blocks.md` | exists; has a heading line for `$CONSTITUTION_BLOCK` and one for `$QUALITY_GATE_BLOCK`, once each |
| AC2 | scan of `phases commands templates skills scripts` | each variable has exactly one definition heading in the plugin tree (the issue's "was/should be" grep) |
| AC3 | the `$CONSTITUTION_BLOCK` section | names `constitution_path` and `bytedigger.json` |
| AC4 | the `$QUALITY_GATE_BLOCK` section | contains `Boy Scout`, `TEST INTEGRITY`, the rule that code, not test assertions, is fixed, and says the TEST INTEGRITY part is added by the orchestrator (no claim that the engine writes it) |
| AC5 | every line of `phases/phase-5-implement.md` that mentions either variable | also names `templates/injection-blocks.md`, outside any double-quoted string |
| AC5b | Assembly paragraph; constitution default | the document names `RED`, `Gherkin`, `GREEN`, the order, and `./constitution.md` |
| AC6 | every `$..._BLOCK` token used in the plugin docs | has a definition heading (no third undefined block can appear) |
| AC7 GUARD | Boy Scout sentence | the same sentence is in the document and in the engine's `QUALITY_GATE_TEXT` (drift guard, green now) |
| AC8 | `CHANGELOG.md` `[Unreleased]` | mentions `bd#128` |

## Rollback

Revert the commit; nothing else reads the document.
