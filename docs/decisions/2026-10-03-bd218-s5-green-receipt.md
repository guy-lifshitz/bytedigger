# bd#218 step 5 — the engine writes the phase-green receipt before the integrity gate (SHADOW, add-only, no LLM)

**Status: r2 (amended after gate r1: diff-path contract, minors)** · **Tier:** 3 (two production files, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `preflight.run_engine_preflight` — the one function that runs the cheap deterministic steps on explicit
fields and writes the receipt; s5 only adds a `phase` argument to it. **Source:** bd#218, `2026-10-03-bd-ladder-map.md` (rollout step 5,
choice A by MGR 2026-10-03); follows s1 (#228) and s2 (#230, `2026-10-03-bd218-s2-receipt-producer.md`).
**Owner:** bd-ladder lot · **expires:** 2026-10-17 (+14 d): by then either the green rung has shadow evidence and a keep/convert decision, or it is removed.

## §1 Problem (measured on cf9d34a)

1. The integrity gate (`phase_5_integrity._invoke_integrity_llm`, hard) goes straight to the LLM; no script result is recorded next to it
   (`git grep -n "preflight" engine_py/bytedigger_engine/workflows/phase_5_integrity.py`: 0 hits). The ladder map lists it as "none called ahead".
2. `receipt_rung("green", tree)` would read `missing` or `stale`: the engine writes only a `phase: red` receipt (s2), and HEAD moves after GREEN.
   `run_engine_preflight` hard-codes `phase="red"`.

## §2 Design — ADD-ONLY, SHADOW

**The integrity gate runs exactly once, whatever the producer or the rung says. Nothing is skipped, removed or reordered. No LLM, no classifier, no test run.**

### op1 `run_engine_preflight(top, red_tests, spec_text, *, base=None, phase="red")`
- `phase` is `"red"` (default, s2 behaviour byte-for-byte) or `"green"`; any other value → failure result (`_CODE_USAGE`, as `run_preflight` for a bad `--phase`), no receipt, old receipt removed.
- With `"green"` the same steps run (`syntax`, `stub`, `facts`): `syntax` covers every changed file against the merge base (so the GREEN production files), `stub` re-lints the test files named in `red_tests`, `facts` renders the green facts pack. The receipt carries `phase: "green"`, `producer: "engine"`, `not_run == ["cite","tier","scoped","siblings","prescreen"]`.
- The receipt file is the single `receipt_path(top)`: a green receipt replaces the red one; a re-entered pre-GREEN gate writes red again (s2 deletes and rewrites). Readers already compare `phase`, so a red reader sees `missing` after a green write, not a wrong `fresh`.

### op2 call in `_invoke_integrity_llm`
After the NO_CHANGES short-circuit and before `invoke_llm_subprocess`: a fail-open block (same shape as the s1/s2 block):
- Config `org_config["preflight_rung"]` as in s1/s2: `{"mode": "off"}` does nothing; unknown mode or non-bool `produce` → event status `config-error`; `{"produce": false}` reads only.
- Tree: `lib.git_cwd.resolve_git_cwd_with_source(cfg, prev.data)`; ambient source → no producer, status `ambient-skip`.
- Test paths for the producer (helper `_green_test_paths(tree, diff_path)`; a named helper, one `read_text` of the diff file):
  - Root: `top = git rev-parse --show-toplevel` of `tree` (via `git_port.git_read`); `tree` itself may be a subdirectory. Diff headers are toplevel-relative, so every path is resolved against `top` and handed to the producer as an **absolute** path; the producer is called with `tree` as before.
  - Parse rule: only a header whose remainder is exactly `a/<P> b/<P>` (same `<P>` both sides, unquoted) yields `<P>`. Any other header — C-quoted (`core.quotePath`, non-ASCII, tab, `"`), a rename (`a/X b/Y`), `--no-prefix`/`--relative` output, a path with `..` — is **unparseable and makes the whole scope empty** (producer fails on the empty list, rung reads `missing`); it is never dropped while other paths stay, so a mocking file next to a clean one cannot produce `fresh`.
  - Keep a parsed path only if `realpath(top/<P>)` is an existing file under `realpath(top)`; a deleted/absent file drops out (a deletion is not a mock risk); a symlink or path that resolves outside `top` makes the scope empty.
  - `diff_path` absent or unreadable → scope empty → `missing`. `top` unresolvable → status `error`.
  - Empty list → the producer fails (empty scope), the rung reads `missing`.
- Bindings: the producer is called as `preflight.run_engine_preflight(...)` (module attribute, as `phase_5_implement`), and `_emit_safe` is a module-global of `phase_5_integrity`. The producer call has its own swallow (as s2), so a raising producer leaves the rung to read `missing`; any other exception in the block → status `error`.
- Producer `spec_text` is `""` (the integrity step reads no spec text; `facts` only quotes it).
- Then `preflight.receipt_rung("green", tree)`; emit `_emit_safe("preflight_receipt", {"status", "red_step", "phase": 5, "cycle": 1, "gate": "integrity"})` (`cycle` = `prev.data.get("cycle", 1)`); add `extra_data["preflight"] = {"status", "red_step"}` to the LLM call.
- Fail-open: any exception → status `error`, gate runs once. The `reroll_until_verdict` attempt is the same closure; the block runs once per step call, not per re-roll.
- Resume replays the cached step result: neither producer nor rung re-runs (as s1/s2).

### Out of scope
Running tests, `cite`, `tier`, a classifier; any LLM call; skipping or replacing the gate; fix-integrity gate (`phase_6_fix_integrity`, a later step); changing reject rows or `reject_stats`; the `StepContract` list.

### Limit stated
A `fresh` green receipt means syntax, stub and facts passed on the tree the integrity gate judged; it does not mean tests ran. An integrity REJECT on `fresh` shows the gate caught something these three steps did not, not something no script could.

## §3 Acceptance criteria

- **AC1** In a tmp git repo with a clean test file and a changed production file, `run_engine_preflight(..., phase="green")` writes `phase: green`, `ok: true`, `producer: engine`, steps syntax/stub/facts; `receipt_rung("green", repo)` is `fresh` and `receipt_rung("red", repo)` is `missing`.
- **AC2** Default call (no `phase`) is unchanged: `phase: red`, same bytes as s2 (`test_bd218_s2_receipt_producer.py` passes without edits).
- **AC3** A green test file that mocks its unit under test → `receipt_rung("green")` is `red` with `red_step == "stub"`, `facts` skipped. A changed production `.py` with a syntax error → `red_step == "syntax"`.
- **AC4** `phase="bogus"` → failure result, no exception, no receipt, an existing receipt removed.
- **AC5** No subprocess whose command contains `pytest` or `bun test` is started.
- **AC6 (reachability)** `_invoke_integrity_llm`, default mode, non-ambient real-repo `git_cwd`, non-empty diff naming an existing test file: a `preflight_receipt` event with `gate == "integrity"` and `status == "fresh"`, `extra_data["preflight"]["status"] == "fresh"`, the LLM called once. With a mocking test file: `red` / `red_step == "stub"`, LLM still once.
- **AC7** `{"produce": false}` → producer not called, event `missing` on a repo without receipt; `{"mode": "off"}` → no producer, no event; non-bool `produce` or unknown mode → `config-error`; LLM once each.
- **AC8** Producer raising (patched) → no exception out of the step, rung runs, LLM once.
- **AC9 (add-only)** All `invoke_llm_subprocess` kwargs minus `extra_data["preflight"]` are equal for produce on, `produce: false`, `fresh` and `red`; prompt and `stable_prefix` byte-equal.
- **AC10** Ambient git cwd (no `git_cwd`) → producer not called, status `ambient-skip`, LLM once.
- **AC11** NO_CHANGES short-circuit (`verdict_override`): no producer, no rung, no event, LLM not called (as today).
- **AC12** Diff naming only deleted/missing test files → status `missing`, never `fresh`; LLM once.
- **AC13** `reroll_until_verdict` with two attempts: the producer runs once, the event is emitted once, the LLM called twice (the re-roll is unchanged).
- **AC15** Subdirectory `git_cwd`: repo with the mocking test file under `sub/`, `git_cwd = repo/sub`, the diff built with toplevel-relative headers → event `red`, `red_step == "stub"` (not `missing`); a clean file → `fresh`.
- **AC16** A diff with a C-quoted header (non-ASCII filename) for a mocking test file plus a clean unquoted test file → status `missing`, never `fresh`. Same for a rename header and a header whose path resolves outside the toplevel (symlink).
- **AC17** Absent/unreadable `diff_path` → `missing`, no exception, LLM once.
- **AC14** Sibling tests pass without edits: `test_phase_5_integrity.py`, `test_GH781_integrity_verdict_forcing.py`, `test_GH786_integrity_completeness_gate.py`, `test_090ED35B_integrity_verdict_trailing.py`, `test_bd_red_test_integrity.py`, `test_phase_5_integrity_schema_smoke.py`, plus `test_bd218_s1_preflight_rung.py`, `test_bd218_s2_receipt_producer.py`, `test_bd164_preflight.py`, and the six inventory-lint tests (s3 list) after the class-I entry below.

## §4 Files

In scope: `engine_py/bytedigger_engine/preflight.py` (the `phase` argument only), `engine_py/bytedigger_engine/workflows/phase_5_integrity.py` (one helper plus the call in `_invoke_integrity_llm`), `engine_py/bytedigger_engine/conformance/class_i_inventory.json` (entries for any new `read_text`/`git_read` in the helper, class `not-prompt`), new `engine_py/tests/test_bd218_s5_green_receipt.py`, `CHANGELOG.md`.
NOT in scope: `check_ladder.py`, `reject_log.py`, `reject_stats.py`, `workflows/engine.py`, `phase_5_implement.py`, `phase_6_fix_integrity.py`, the `StepContract` list.
Sibling-test audit (§1a): the AC14 list plus `test_bd141_p4d_role_template_injections.py` (real non-ambient repo, producer now runs), `test_llm_subprocess_hard_gate.py`, `test_llm_subprocess_allowed_tools.py` (nonexistent diff path), `test_gh705_callsite_stable_prefix.py` (text-scans `stable_prefix=prev.data.get("stable_prefix"` in the body; must stay verbatim), `test_GH1399_advisory_format_terminal.py`, plus `grep -l "_invoke_integrity_llm\|run_engine_preflight" engine_py/tests`; any sibling that drives `_invoke_integrity_llm` on a real non-ambient repo now sees a producer run — audit for pre-staged receipts (the producer deletes the old one first).

## §5 Provenance (Guy 2026-10-03)

Removes nothing. The steps reused were introduced by bd#164 (PR #186); the integrity gate itself was ported from HAL (`phase-5-implement.md` Step 3.5, GH381 era; bd git history shows it from the extraction commit da4d41e) and is untouched. introduced: bd#218 s2 (#230) -- what it guarded: n/a (new evidence rung) -- why it can go: expires 2026-10-17 unless the shadow table shows it earns a place; it only ever becomes a cheaper pre-check, never a gate.

## §6 Cost note

No LLM $. Added wall time per integrity call: one `compile`/`bash -n` per changed file, one AST lint per test file, one `facts_pack.collect`. Not measured; no number claimed.
