# bd#218 step 1 — preflight receipt rung in front of the validation gate (add-only)

**Status: r1 (draft for the Opus gate)** · **Tier:** 2 (one function in `preflight.py`, one call block in `workflows/phase_5_implement.py`, Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `preflight.receipt_rung` — the one function that turns
(phase, work tree) into the rung record the gate step emits. The phase-5 call adds only the event.
**Side of the seam:** engine. **Source:** bd#218, `2026-10-03-bd-ladder-map.md`; builds on bd#141 item 3
(`check_ladder`, PR #144) and bd#164 (`preflight`, PR #186).

## §1 Problem (measured on origin/main at branch point)

1. `_invoke_validation_llm` (phase 5, pre-GREEN) calls `check_ladder.prescreen` in shadow mode only, and
   only when `prescreen.classifier_cmd` is configured, with `findings=[]`. The script rung is empty.
2. `preflight.verify_receipt` is not called by any engine phase (`git grep verify_receipt -- engine_py/bytedigger_engine/workflows`: 0 hits).
   A host that ran `preflight` before the gate leaves a receipt the engine never looks at.
3. Consequence: no record, per run, of whether the cheap deterministic checks had passed on the exact tree the
   gate is about to judge. The later steps of bd#218 (and the lot-assume audit) need that record.

## §2 Design — ADD-ONLY

**Nothing is removed, skipped or reordered. The validation gate runs exactly once per call, whatever the rung says.**
Skipping the gate on a fresh green receipt is paused until Guy answers "yes" on the lot-assume audit.

### op1 `preflight.receipt_rung(phase, toplevel) -> dict`
Returns `{"status": "fresh"|"stale"|"red"|"missing"|"error", "phase": phase}`. Read-only: calls `verify_receipt`
and nothing else (no test run, no classifier, no network, no write). Never raises: any exception → `status: "error"`.
Stdlib only; added to the existing module, so `core_manifest.json` and `mypy-strict-modules.txt` need no change.

### op2 call site in `_invoke_validation_llm`
Before `invoke_llm_subprocess`, after the existing shadow block: read `cfg.get("preflight_rung")`.
- absent or `{"mode": "verify"}` → default. Resolve the work tree with `_resolve_git_cwd(ctx, prev)`, call
  `receipt_rung("red", tree)`, emit `_emit_safe("preflight_receipt", {...})` with `status`, `phase: 5`, `cycle`, `gate: "validation"`,
  and add `extra_data["preflight"] = {"status": ...}`.
- `{"mode": "off"}` → nothing is called or emitted; `extra_data` is today's.
- any other mode value, or a non-dict value → event with `status: "config-error"`, gate runs.
- the whole block is wrapped so an exception emits `status: "error"` and never blocks the gate (same shape as the shadow block).
`extra_data["preflight"]` is read by no consumer in this PR.

### Out of scope (stated so nothing is silently dropped)
- Running `run_preflight` from the engine (the receipt is only read). No new subprocess except the git reads inside `verify_receipt`.
- Any LLM call, any classifier, any change to `check_ladder`, to the shadow block, to gate prompts or retries.
- Skipping or removing any gate, check or flag (paused, see above). Wording/citation-format checks (Guy 2026-10-03).

## §3 Acceptance criteria

- **AC1** `receipt_rung` returns one of the five statuses; for a path that is not a git repo, a missing receipt, a corrupt receipt, it returns `missing`/`error`, never raises.
- **AC2 (production side effect, §1l)** In a tmp git repo with a real spec, `run_preflight(spec, "red", cwd=repo)` writes a real receipt; `receipt_rung("red", repo)` is `fresh`; after editing a tracked file it is `stale`; after a red preflight it is `red`; with a receipt of phase `green` asked for `red` it is `missing`.
- **AC3** `_invoke_validation_llm` with default config emits exactly one `preflight_receipt` event before the gate call, carrying `status`, `phase: 5`, `cycle`, `gate: "validation"`; `invoke_llm_subprocess` is called exactly once for each of fresh/stale/red/missing.
- **AC4** `receipt_rung` raising (monkeypatched) → event `status: "error"`, gate still called once, step result unchanged.
- **AC5** `{"mode": "off"}` → no `preflight_receipt` event, `receipt_rung` not called, `invoke_llm_subprocess` kwargs equal today's (`extra_data` has no `preflight` key).
- **AC6** unknown mode string and non-dict config → event `status: "config-error"`, gate called once.
- **AC7** the rung starts no LLM call and no classifier: with `prescreen.classifier_cmd` unset, the only `invoke_llm_subprocess` call is the gate's.
- **AC8** order: with `prescreen.classifier_cmd` set, shadow events and the `preflight_receipt` event both precede the gate call; the shadow verdict still does not skip the gate.
- **AC9** `test_bd141_check_ladder.py` (L16/L17 family) and `test_bd164_preflight.py` pass unchanged.

## §4 Files

In scope: `engine_py/bytedigger_engine/preflight.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py`,
new `engine_py/tests/test_bd218_s1_preflight_rung.py`, `CHANGELOG.md`.
NOT in scope: `check_ladder.py`, `workflows/engine.py`, `phases/`, `error_codes.py`, every other workflow file.
Sibling-test audit (§1a) from this list, run with `--require-clean` before freeze: `test_bd141_check_ladder.py`, `test_bd164_preflight.py`, the phase-5 validation-gate tests (`grep -l _invoke_validation_llm engine_py/tests`).
The PR must show zero deleted lines in `workflows/` and `check_ladder.py` (add-only).

## §5 Provenance (Guy 2026-10-03)

Removes or simplifies nothing, so no "introduced:" line is owed. The gate itself: introduced in HAL as
`never_skip_opus_validation_gate`; bd history from da4d41e (2026-07-16); incident provenance pending the lot-assume audit.

## §6 Verification

Scoped: `pytest engine_py/tests/test_bd218_s1_preflight_rung.py` + the §4 siblings; `mypy --strict preflight.py`; full suite on CI (delta, §1r).
Baseline for the cost claim: none made — this step adds an event, no $ change is claimed.
