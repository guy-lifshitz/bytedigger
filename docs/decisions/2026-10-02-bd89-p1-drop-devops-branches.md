# bd#89 P1: drop devops_scan / devops_pipeline / canary / smoke / artifact-detect from the engine and the /build flow

**Status: DRAFT r2 (gate r1 REJECT → 2 blockers + minors, see `2026-10-02-bd89-p1-gate-r1.md`)** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (scope-of-build decision) ·
**Chokepoint:** the stage registry `register_all` (`engine_py/bytedigger_engine/workflows/__init__.py:25`). It is the only place an engine stage becomes reachable. The /build orchestrator flow `commands/build.md` is the md-side counterpart.
**Side of the seam (decision 2026-07-26 §7.1):** engine + orchestrator md. No host mechanism is needed.
**Source:** bd#89 checklist row 5 ("never exercised in the measured run window … drop from the default path"). The split plan is in issue comment 5947880163: P1 covers row 5; P2 covers rows 1 and 4 (phases 1–4, spec_lite, SIMPLE fast path); P3 covers rows 2, 3, 6 and 7.

## §1 Problem (measured on `24dbd13`)

1. The registry registers five devops-only stages (`workflows/__init__.py:29,36,37,39,43`): `phase_0_6_artifact_detect`, `phase_5_devops_scan`, `phase_devops_pipeline`, `phase_5_integration_canary` and `phase_6_smoke`. Nothing in `commands/`, `phases/`, `skills/` or `scripts/` invokes them by name.
2. Nothing in the engine writes `org_config["artifact_type"]`. The only producer is the dropped artifact-detect stage's own result. Every `artifact_type` consumer is therefore dead code in practice:
   - `_select_reviewers` / `_review_plan` devops rows (`workflows/phase_6_review.py:261,466-467,488-511,792-794,1131,1686-1687`).
   - `get_standards_context` (`workflows/_standards_context.py`), which also hard-wires a HAL path, `SYSTEM/cli/build/devops-prompt-context.ts` (`:55`). It is called from `phase_45_spec.py:85-87,1301-1303` and `phase_5_implement.py:149-151,1449-1451,7461-7463`.
3. `_write_spec_doc` (`phase_45_spec.py:1511`) parses a "Canary Integration" block (`_parse_canary_integration`, `:1465`). It then writes `integration/canary-meta.json` (`CANARY_META_RELPATH`, `:345`) and emits `canary_integration_parsed` (`:1622-1630`, `:1673-1681`). The only consumer was `phase_5_integration_canary`.
4. Dead registry residue would fail CI once the modules are gone:
   - error codes `E_CANARY_*` (`error_codes.py:40-42`), `E_DEVOPS_SCAN_*` (`:53-54`) and `E_SMOKE_*` (`:232-233`), plus both `ERROR_CODES.md` copies;
   - flags `HAL_DEVOPS_SCAN_{GATE,CONFIG,ALLOWLIST}` and `ROUTED_MODULES` (`flags_catalog.py:94,190-206`);
   - `core_manifest.json:57,62,77,84,87`.
5. The md flow still describes devops:
   - DevOps detection in Phase 0 (`commands/build.md:49`, `phases/phase-0-classify.md`);
   - "5.6 DevOps Validation (if profile=devops)" (`build.md:121`, `phases/phase-5-implement.md:383-390`);
   - devops extra reviewers (`phases/phase-6-review.md:212`);
   - the `devops_profile` read in `scripts/build-gate.sh:160-167`;
   - the "DevOps override" in `templates/dynamic-context.md:32`.

## §2 Design: delete, don't flag

Row 5 allows "reintroduce only behind an explicit flag *if a real use case shows up*". None has, so nothing is kept behind a flag. That also avoids creating a new default-off flag, which row 7 forbids.

- **op1 (registry):** delete the five modules and their imports and registrations. `register_all` registers exactly the frozen set in AC1.
- **op2 (artifact_type):** delete `_standards_context.py` and its three call sites. `_select_reviewers(complexity, fanout="single")` loses the `artifact_type` parameter. `_review_plan(ctx, complexity)` drops it too. `_ROW_DEVOPS_REVIEWER` and `_LINE_COMPOSITE_DEVOPS` are deleted. An `artifact_type` key in `org_config` is ignored.
- **op3 (canary sidecar):** delete `CANARY_META_RELPATH`, `_CANARY_EVENT_TYPE_RE`, `_parse_canary_integration`, the sidecar write, the `canary_event_type` data key and the `canary_integration_parsed` emit at both sites in `_write_spec_doc`. Any "Canary Integration" prompt text in phase_45_spec is deleted as well.
- **op4 (residue):**
  - Remove the 7 error codes from `error_codes.py` and from both `ERROR_CODES.md` copies, which stay byte-identical.
  - Remove the 3 `HAL_DEVOPS_SCAN_*` flags and the `ROUTED_MODULES` entry.
  - Remove the 5 entries from `core_manifest.json`. `_standards_context.py` is not listed there; if it is, remove it as well.
  - Delete the `("CANARY_", "engine")` prefix rule in `lib/dispatcher_report.py:80`. It becomes dead once the `E_CANARY_*` codes are gone.
  - Remove the `devops-*` rows from the `test_engine_path_closure.py` allowlist (`:50-55`). That edit is test-side and goes in the RED commit.
- **op5 (md flow):**
  - Remove DevOps detection, the `devops_profile` state field, 5.6 DevOps Validation and the devops extra reviewers from `commands/build.md`, `phases/phase-0-classify.md`, `phases/phase-5-implement.md`, `phases/phase-6-review.md`, `skills/bytedigger/SKILL.md` and `templates/dynamic-context.md`.
  - In `build-gate.sh:160-167`, delete the whole M7 block. `DEVOPS_EXTRA_REVIEWERS` is set and never read anywhere (gate r1 m1), so the block is dead code, and that includes its `security_classification` read. Also delete the "DevOps override" lines in `templates/dynamic-context.md:30-32` and the devops mentions in `phases/phase-5-implement.md:451-452`. AC8 is the complete list: every `devops` hit in the AC8 files goes.
  - `docs/*.md` (not `docs/decisions/`), `README.md` and `examples/` mentions are removed in the doc dance. They are not an AC.

Provider principles: P1 adds no LLM call and no provider dependency. It removes one hard HAL-path subprocess (the `.ts` shim). All new tests are deterministic and run with no `claude` on PATH and no API key (AC9).

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p1_devops_dropped.py`

- **AC1 (registry, side-effect):** `register_all(fake_engine)` records exactly `{echo, phase_0_research, phase_05_inject, phase_1_discovery, phase_2_explore, phase_3_clarify, phase_4_architect, phase_45_spec, phase_45_spec_lite, phase_5_implement, phase_5_integrity, phase_6_fix_integrity, phase_6_review, phase_6_review_simple_fastpath, phase_7_synthesize, phase_8_post_deploy}`. P2 and P3 shrink this set and update this AC.
- **AC2:** none of the 5 module files or `workflows/_standards_context.py` exists on disk. `importlib.util.find_spec` on each dotted name returns `None`. `core_manifest.json` lists none of them.
- **AC3:** the 7 codes are absent from `error_codes.ERROR_CODES` (or the registry object `error_codes.py` exports) and from both `ERROR_CODES.md` files.
- **AC4 (generic invariant):** every `module` path in `flags_catalog.FLAGS` and every entry of `ROUTED_MODULES` resolves to an existing file under `engine_py/bytedigger_engine/` or `engine_py/`. `SYSTEM/` host paths are skipped. The exceptions are a frozen set of two entries that were already dangling at base: `flags_catalog.py:644` `"engine-py-audit-gate.py + workflows/phase_5_implement.py"` and `:788` `"canary.sh"`. Both are row-7 flag residue and P3 resolves them. The test asserts that the dangling set is a subset of that frozen set. No `HAL_DEVOPS_SCAN_*` key exists, and no entry names a P1-dropped module.
- **AC5 (side-effect):** `_select_reviewers` has no `artifact_type` parameter (checked with `inspect.signature`). The phase 6 review prompt is built for a ctx with `org_config={"artifact_type": "dockerfile"}` through `_review_plan` / the prompt builder the bd139 tests use. Its dispatch table contains no `devops` (case-insensitive), and the reviewer count is 1.
- **AC6 (disk side-effect):** `_write_spec_doc` runs on a spec whose raw text has a valid "Canary Integration" block with `event_type: foo`. It writes no `integration/canary-meta.json` under the scratchpad. It emits no `canary_integration_parsed` event, which the test checks by reading the event log. Its `StepResult.data` has no `canary_event_type` key. Both branches are covered: the surgical-revise path and the normal path.
- **AC7:** `phase_45_spec` and `phase_5_implement` have no attribute `get_standards_context`. With `subprocess.run` patched to record calls, building the spec-writer prompt, the RED prompt and the GREEN prompt (`phase_5_implement.py:7461` `_build_green_prompt`) for a ctx with `artifact_type` set makes no call whose argv mentions `devops-prompt-context`.
- **AC8 (md):** a case-insensitive grep for `devops` returns nothing in `commands/build.md`, `phases/*.md`, `skills/bytedigger/SKILL.md`, `templates/*` or `scripts/build-gate.sh`. A grep for `integration_canary|phase_6_smoke|devops_scan|artifact_detect` returns nothing in those files or in `scripts/ts/*.ts`.
- **AC9 (provider-agnostic):** the RED file's tests pass under `monkeypatch.delenv("ANTHROPIC_API_KEY")` with a PATH that has no `claude`. An autouse fixture sets this up.
- **AC10:** `lib/dispatcher_report.py` has no `CANARY_` prefix rule. `classify_error_code` still classifies a live engine code as `engine`.

## §4 Out of scope (§1v)

- Phases 1–4, spec_lite, the SIMPLE fast path and `findings-*.md` gates (P2).
- phase_6 `parallel` fan-out, the decorrelated verifier, reviewer-count keys and the `security_classification` +3 logic (P3).
- phase_7_synthesize and the surgical/restricted revise (P3).
- Enforce-flag resolution (P3).
- `lib/tree_root.py` stays: tests and `test_engine_path_closure` still use it.
- `docs/decisions/*` and CHANGELOG history stay untouched.

## §5 Scope list

Prod deletions:
- `engine_py/bytedigger_engine/workflows/{phase_0_6_artifact_detect,phase_5_devops_scan,phase_devops_pipeline,phase_5_integration_canary,phase_6_smoke,_standards_context}.py`

Prod edits:
- `workflows/__init__.py`, `workflows/phase_6_review.py`, `workflows/phase_45_spec.py`, `workflows/phase_5_implement.py`
- `error_codes.py`, `engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md`
- `flags_catalog.py`, `engine_py/core_manifest.json`
- `scripts/build-gate.sh`
- the md list in op5

Test retirements (RED commit; §1a sibling audit):
- Delete these files, whose sole subject is a dropped stage: `test_phase_5_devops_scan.py`, `test_phase_0_6_artifact_detect.py`, `test_phase_5_integration_canary.py`, `test_360F99CE_phase6_smoke_graceful.py`, `test_bd1_phase6_smoke_skip_status.py`, `test_phase_5_canary_event_type_from_spec_36269734.py` and `test_standards_context.py`.
- Edit these mixed files:
  - `test_bd97_host_path_depth.py`: retire the `phase_6_smoke`-anchored ACs. **Port** base AC2, AC3 and AC3b–d so they call `lib/tree_root.resolve_tree_root` directly. Those cases cover: never raises at any depth, a marker beats a nested `.git`, a worktree `.git` file is recognised, and the package-root fallback (gate r1 B2).
  - `test_bd139_single_reviewer.py`: retire the devops-row cases (`:32-34,95-111,181-192`) and keep the no-devops assertions.
  - `test_ccbb65dc_straggler_watchdog.py:417`: update the call signature.
  - `test_subagent_return_discipline_FD2592D9.py::test_ac8_canary_sidecar_written_from_file_sourced_spec`: retire.
  - `test_bd147_injected_segments.py:372`: drop the `get_standards_context` monkeypatch.
  - `test_dispatcher_report.py:69`: re-point it to a live code.
  - `test_engine_path_closure.py:50-55`: drop the devops rows.
  - `test_derive_state.py:24-28`: change the workflow name to the live `phase_6_review`.
  - `test_bd119_role_template.py`: re-check `:1224,1241`.
- Expected red before GREEN: `test_engine_path_closure.py`, because its allowlist rows are already gone. The baseline delta must account for it.
- Run `pytest --collect-only -q` after the edits; it must report zero collection errors.
