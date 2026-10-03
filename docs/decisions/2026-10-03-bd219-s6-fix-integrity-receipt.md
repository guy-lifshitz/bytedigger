# bd#219 step 6 — the engine writes the phase-green receipt before the fix-integrity gate (SHADOW, add-only, no LLM)

**Status: r1 (draft for gate)** · **Tier:** 3 (one production file + changelog, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `_invoke_fix_integrity_llm` in `phase_6_fix_integrity.py` — the single call site of the fix-side hard gate.
**Source:** bd#219 ladder map (`2026-10-03-bd-ladder-map.md`), next rung after s5 (#235, `2026-10-03-bd218-s5-green-receipt.md`); same template.
**Owner:** bd-ladder lot · **expires:** 2026-10-17 (+14 d): by then the fix-side green rung has shadow evidence and a keep/convert decision, or it is removed.

## §1 Problem (measured on 440db6e)

The fix-integrity gate (`_invoke_fix_integrity_llm`, hard, `gate_label="fix_integrity"`) goes straight to the LLM; no script result is recorded next to it
(`git grep -n "preflight" engine_py/bytedigger_engine/workflows/phase_6_fix_integrity.py`: 0 hits). Phase 5's integrity gate has had the green rung since s5; the fix side has none, so the shadow table cannot compare fix-side REJECTs against script evidence.

## §2 Design — ADD-ONLY, SHADOW, mirror of s5

**The fix-integrity gate runs exactly once, whatever the producer or the rung says. Nothing is skipped, removed or reordered. No LLM, no classifier, no test run. `preflight.py` is NOT changed** (`run_engine_preflight(..., phase="green")` exists since s5).

### op1 call in `_invoke_fix_integrity_llm`
After the `verdict_override` (NO_CHANGES) short-circuit and before `reroll_until_verdict`: a fail-open block with the exact shape of the s5 block in `phase_5_integrity._invoke_integrity_llm`:
- Config `cfg["preflight_rung"]`: `{"mode": "off"}` does nothing (no producer, no rung, no event); unknown mode or non-bool `produce` → event status `config-error`; `{"produce": false}` reads only.
- Tree: `resolve_git_cwd_with_source(cfg, prev.data)`; ambient source (`is_ambient_git_cwd`) → no producer, status `ambient-skip`.
- Test paths: the s5 helper is **reused, not copied**: `from bytedigger_engine.workflows.phase_5_integrity import _green_test_paths` (precedent: the `_autocommit_fix_tail` import from `phase_6_review`; module-level, no cycle: `phase_5_integrity` imports nothing from phase 6). Called as `_green_test_paths(tree, prev.data.get("diff_path"))`. The fix diff (`pre_fix..fix`, filtered by `diff_patterns`) has the same `diff --git a/<P> b/<P>` headers; the s5 parse rule applies unchanged (unparseable header / `..` / outside toplevel empties the scope → `missing`; absent files drop out). The helper's own inventory entries already exist; **no new `read_text`/`git_read` is added in phase 6**, so `class_i_inventory.json` is unchanged.
- Producer: `preflight.run_engine_preflight(tree, paths, "", phase="green")` called as a module attribute (`from bytedigger_engine import preflight`, so tests patch `preflight.run_engine_preflight`), inside its own swallow; then `preflight.receipt_rung("green", tree)`.
- Emit `_emit_safe("preflight_receipt", {"status", "red_step", "phase": 6, "cycle": cfg.get("cycle", 1), "gate": "fix_integrity"})`; add `extra_data["preflight"] = {"status", "red_step"}` to the LLM call, inside the `_attempt` closure (spread like s5). Both `_emit_safe` names already exist in the module.
- Fail-open: any exception → status `error` (and `extra_data["preflight"]` = error record), the gate runs once. The block runs once per step call, not per re-roll. Resume replays the cached step result (neither producer nor rung re-runs).
- The receipt file is the single `receipt_path(top)`; the fix-side green write replaces the phase-5 green one for the same tree (same `phase: green`); readers compare `phase`/HEAD, so freshness is judged on the post-fix HEAD the gate saw.

### Out of scope
Running tests, `cite`, `tier`, a classifier; any LLM call; skipping/replacing the gate; changing `preflight.py`, `phase_5_integrity.py`, reject rows, `reject_stats`, the `StepContract` list, `check_ladder.py`, `engine.py`. A new `phase="fix"` receipt value (would touch `_PHASES` and every reader) is not introduced.

### Limit stated
A `fresh` receipt means syntax, stub and facts passed on the post-fix tree; it does not mean tests ran. `missing` also covers an empty scope (no parseable test path, unparseable header, absent diff). Because the fix diff is filtered to test-like paths, a fix that edits only production code yields an empty/`missing` scope by construction. The receipt is `phase: green`, so a fix-side receipt overwrites a phase-5 green one and is indistinguishable from it on disk; the event's `gate` field is the discriminator.

## §3 Acceptance criteria

- **AC1 (reachability)** `_invoke_fix_integrity_llm`, default mode, non-ambient real-repo `git_cwd`, non-empty diff file naming an existing clean test file: a `preflight_receipt` event with `gate == "fix_integrity"`, `phase == 6`, `status == "fresh"`; `extra_data["preflight"]["status"] == "fresh"`; the LLM called once. With a test file that mocks its unit under test: `red` / `red_step == "stub"`, LLM still once.
- **AC2** `{"produce": false}` → producer not called, event `missing` on a repo without receipt; `{"mode": "off"}` → no producer, no event; non-bool `produce` or unknown mode → `config-error`; LLM once each.
- **AC3** Producer raising (patched) → no exception out of the step, rung runs, LLM once.
- **AC4 (add-only)** All `invoke_llm_subprocess` kwargs minus `extra_data["preflight"]` are equal for produce on, `produce: false`, `fresh` and `red`; prompt byte-equal.
- **AC5** Ambient git cwd → producer not called, status `ambient-skip`, LLM once.
- **AC6** NO_CHANGES short-circuit (`verdict_override`): no producer, no rung, no event, LLM not called (as today).
- **AC7** Diff naming only deleted/missing test files → `missing`, never `fresh`; absent/unreadable `diff_path` → `missing`, no exception, LLM once.
- **AC8** `reroll_until_verdict` with two attempts: producer once, event once, LLM twice.
- **AC9** C-quoted header for a mocking file plus a clean file → `missing`, never `fresh`; a `..` header → `missing`.
- **AC10** `git_cwd` a non-git directory (not ambient): no exception, status `error`, producer not called, LLM once.
- **AC11** No subprocess whose command contains `pytest` or `bun test` is started.
- **AC12** `phase_6_fix_integrity` imports `_green_test_paths` from `phase_5_integrity` (identity: `phase_6_fix_integrity._green_test_paths is phase_5_integrity._green_test_paths`) and `preflight.py`, `phase_5_integrity.py` are byte-unchanged by this lot.
- **AC13** Sibling tests pass without edits: `grep -l "phase_6_fix_integrity\|_invoke_fix_integrity_llm" engine_py/tests`, `test_bd218_s5_green_receipt.py`, `test_bd218_s2_receipt_producer.py`, `test_bd218_s1_preflight_rung.py`, `test_gh381_git_cwd_resolver.py`, the six inventory-lint tests, and any text-scan test over `phase_6_fix_integrity.py` (stable-prefix/injection scans).

## §4 Files

In scope: `engine_py/bytedigger_engine/workflows/phase_6_fix_integrity.py` (two imports + one block in `_invoke_fix_integrity_llm`), new `engine_py/tests/test_bd219_s6_fix_green_receipt.py`, `CHANGELOG.md`.
NOT in scope: everything else (see Out of scope). Sibling audit (§1a): AC13 list; any sibling driving `_invoke_fix_integrity_llm` on a real non-ambient repo now sees a producer run — audit for pre-staged receipts (the producer deletes the old one first).

## §5 Provenance (Guy 2026-10-03)

Removes nothing. introduced: bd#219 s6 -- what it guarded: n/a (new evidence rung) -- why it can go: expires 2026-10-17 unless the shadow table shows it earns a place; only ever a cheaper pre-check, never a gate.

## §6 Cost note

No LLM $. Added wall time per fix-integrity call: one `compile`/`bash -n` per changed file, one AST lint per test file, one `facts_pack.collect`. Not measured; no number claimed.
