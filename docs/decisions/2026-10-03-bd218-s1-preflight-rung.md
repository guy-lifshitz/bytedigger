# bd#218 step 1 — preflight receipt rung in front of the validation gate + shadow reject-log evidence (add-only)

**Status: r3** (r2 added the shadow reject-log collection after the lot-assume audit hal#2320, pause lifted by Guy 2026-10-03; gate r1 REJECTED 5 MAJOR + 5 MINOR, see `2026-10-03-bd218-s1-gate-r1.md`, all applied here) · **Tier:** 2 (one function in `preflight.py`, one call block in `workflows/phase_5_implement.py`, Option D) ·
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
Skipping or replacing the gate is a separate PR that must carry the `ladder_table` evidence (audit hal#2320 verdict: the Opus stage stays until the reject logs show a script catches all its findings).

### op1 `preflight.receipt_rung(phase, toplevel) -> dict`
Returns `{"status": "fresh"|"stale"|"red"|"missing"|"error", "phase": phase, "red_step": <name of the first step with status red in the receipt's steps[]>|None}`. `red_step` is read from the receipt even when it is stale (a stale receipt that was red keeps its red step); `None` when the receipt is missing/unreadable or ok. Read-only: calls `verify_receipt` and reads `receipt.json` (no test run, no classifier, no network, no write). Never raises: any exception → `status: "error"`.
Stdlib only; added to the existing module, so `core_manifest.json` and `mypy-strict-modules.txt` need no change.

### op2 call site in `_invoke_validation_llm`
Before `invoke_llm_subprocess`, after the existing shadow block: read `cfg.get("preflight_rung")`.
- key absent, `None`, `{}` or `{"mode": "verify"}` → default. Resolve the work tree with `_resolve_git_cwd_with_source(ctx, prev)`. If the source is ambient (`lib.git_cwd.is_ambient_git_cwd`, GH1220), the receipt of whatever repo the process sits in must not be read: emit the event with `status: "ambient-skip"`, no `extra_data["preflight"]`. Otherwise call
  `receipt_rung("red", tree)`, emit `_emit_safe("preflight_receipt", {...})` with `status`, `red_step`, `phase: 5`, `cycle`, `gate: "validation"`,
  and add `extra_data["preflight"] = {"status": ..., "red_step": ...}` (exactly these two keys: the record rides in every reject row, which `emit_reject_reason` drops whole above 4096 bytes).
- `{"mode": "off"}` → nothing is called or emitted; `extra_data` is today's.
- any other mode value, or a non-dict non-None value → event with `status: "config-error"` (no `extra_data["preflight"]`), gate runs.
- the whole block is wrapped so an exception emits `status: "error"` and never blocks the gate (same shape as the shadow block).
On `error`, `extra_data["preflight"] = {"status": "error", "red_step": None}`. Resume replays the cached step result (`resume_sentinel=True`): no new event, and the record is from the earlier tree. Accepted and stated; such rows are still labelled by the status they were recorded with.

### op2b thread the record to the reject logger (gate r1 MAJOR-1)
`extra_data["preflight"]` does not survive to `_log_validation_reject`: `_write_validation_doc` (and `_verify_validation_citations`) build fresh `data` dicts. Both steps forward `"preflight": prev.data.get("preflight")` **only when the key is present**, so the data of a run without the record is byte-identical to today's.

### op3 shadow reject-log evidence (audit hal#2320: the Opus pre-GREEN gate stays until the reject logs show all its findings are caught by a script)
- `_log_validation_reject` passes `prev.data.get("preflight")` (the op2 record) into `reject_log.record_validation_reject(..., preflight=...)`; the row's `detail` gains `preflight: {"status", "red_step"} | null` (null when the key is absent). No other field of the row changes; logging still never alters the gate outcome (existing swallow).
- `reject_stats.ladder_table(rows) -> list[dict]`: over rows with `phase == "phase_5_implement"` and `reason_code` starting `VALIDATION_`, group by `detail.preflight.status` and `red_step`; a row with no `detail.preflight` key (every historical row) or `null` → status `none`; a `detail.preflight` that is not a dict or has no string `status` → skipped as malformed. Each group `{"status", "red_step", "rejects", "findings_heads": [up to 3 heads]}`, sorted by `rejects` desc then `status`, `red_step` ascending (deterministic ties; `None` sorts as empty string). Pure, never raises on malformed rows (skipped).
- Reading (corrected after gate r1 MAJOR-5): **only `fresh` and `red` carry information.** A REJECT while the receipt is `fresh` = the gate caught something the scripts did not (evidence for keeping the LLM stage). A REJECT with `red` = a script already flagged it. `stale`, `missing`, `none`, `error`, `config-error`, `ambient-skip` = **no evidence either way** and must never be counted as "a script could have caught it".
- Known limit, stated so nobody over-reads the table: no engine code runs `run_preflight`, and `commit_red_tests` moves HEAD right before the gate, so in an engine-driven run the status will mostly be `stale`/`missing`. This step therefore delivers the rung, the plumbing and the table, not yet the evidence. A producer (the engine running the cheap deterministic steps after `commit_red_tests` and writing the receipt) is the next step (s2, own spec and gate); the gate-replacement PR may cite the table only for rows with `fresh`/`red` status and needs enough of them.
- Shadow means: collected only, no behaviour depends on it.

### Out of scope (stated so nothing is silently dropped)
- Running `run_preflight` from the engine (the receipt is only read). No new subprocess except the git reads inside `verify_receipt`.
- Any LLM call, any classifier, any change to `check_ladder`, to the shadow block, to gate prompts or retries.
- Skipping, replacing or removing any gate, check or flag (separate PR with the `ladder_table` evidence). Wording/citation-format checks (Guy 2026-10-03).

## §3 Acceptance criteria

- **AC1** `receipt_rung` returns one of the five statuses; for a path that is not a git repo, a missing receipt, a corrupt receipt, it returns `missing`/`error`, never raises.
- **AC2 (production side effect, §1l)** In a tmp git repo with a real spec, `run_preflight(spec, "red", cwd=repo)` writes a real receipt; `receipt_rung("red", repo)` is `fresh`; after editing a tracked file it is `stale`; after a red preflight it is `red`; with a receipt of phase `green` asked for `red` it is `missing`.
- **AC3** `_invoke_validation_llm` with default config emits exactly one `preflight_receipt` event before the gate call, carrying `status`, `phase: 5`, `cycle`, `gate: "validation"`; `invoke_llm_subprocess` is called exactly once for each of fresh/stale/red/missing.
- **AC4** `receipt_rung` raising (monkeypatched) → event `status: "error"`, gate still called once, step result unchanged.
- **AC5** `{"mode": "off"}` → no `preflight_receipt` event, `receipt_rung` not called, `invoke_llm_subprocess` kwargs equal today's in full (asserted against an explicit expected dict: the six `extra_data` keys `doc_path, spec_path, red_log_path, red_test_paths, cycle, red_commit_sha` plus the fixed call kwargs; not only the key set).
- **AC6** unknown mode string and non-dict config → event `status: "config-error"`, gate called once.
- **AC7** the rung starts no LLM call and no classifier: with `prescreen.classifier_cmd` unset, the only `invoke_llm_subprocess` call is the gate's.
- **AC8** order: with `prescreen.classifier_cmd` set, shadow events and the `preflight_receipt` event both precede the gate call; the shadow verdict still does not skip the gate.
- **AC9a** `record_validation_reject(..., preflight={"status": "fresh", "red_step": None})` writes a reject row whose `detail.preflight` equals it; without the argument `detail.preflight` is `null` and every other field equals today's row.
- **AC9b** `_log_validation_reject` forwards `prev.data["preflight"]` into the row (real reject-log file in a tmp path, rejected-gate path driven end to end); a missing `preflight` key is `null`; a logging failure still does not change the gate outcome.
- **AC9c** `reject_stats.ladder_table` over a mixed fixture (fresh/red/stale/missing/none, non-validation rows, malformed rows) returns the expected groups and counts, ignores the non-validation and malformed rows, and caps `findings_heads` at 3.
- **AC11 (add-only, gate r1 MAJOR-3)** For a `fresh` receipt and for a `red` receipt, the `invoke_llm_subprocess` kwargs in default mode, minus `extra_data["preflight"]`, equal the kwargs with `{"mode": "off"}` (prompt, model, allowed_tools, timeout, hard_gate, gate_label, stable_prefix, injections, the other `extra_data` keys), and the gate is called once in both.
- **AC12 (reachability, gate r1 MAJOR-1)** End to end through `_invoke_validation_llm → _write_validation_doc → _verify_validation_citations → _gate_on_validation` with a FAIL verdict: the reject row written to a real log file has `detail.preflight` non-null and equal to the record. With no record in `extra_data` the intermediate steps' data has no `preflight` key.
- **AC13** `{"mode": "verify"}`, `{}` and `None` behave as the default; `{"mode": "off"}` is the only opt-out.
- **AC14 (ambient cwd, E4)** When the git cwd resolves from an ambient source (no `git_cwd`, no `current_worktree_path`, scratchpad not inside a repo), `receipt_rung` is not called, the event has `status: "ambient-skip"`, the gate runs once.
- **AC15 (legacy rows, E3/E8)** `ladder_table` puts rows with no `detail.preflight` key under `none`, skips rows whose `detail.preflight` is a non-dict or has no string `status`, and orders equal `rejects` counts by `status` then `red_step`.
- **AC16** `receipt_rung` of a stale receipt that was red reports `status: "stale"` and `red_step` = the receipt's first red step.
- **AC10** `test_bd164_preflight.py` and the other sibling files of §4 pass unchanged. `test_bd141_check_ladder.py` passes with exactly one edit: the L16 expected `extra_data` key set gains `"preflight"` (default-on adds the record; the file's other assertions are untouched).

## §4 Files

In scope: `engine_py/bytedigger_engine/preflight.py`, `engine_py/bytedigger_engine/reject_log.py`, `engine_py/bytedigger_engine/reject_stats.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py`,
new `engine_py/tests/test_bd218_s1_preflight_rung.py`, the one-key edit of L16 in `engine_py/tests/test_bd141_check_ladder.py` (AC10), `CHANGELOG.md`. In `phase_5_implement.py`: `_invoke_validation_llm`, `_write_validation_doc`, `_verify_validation_citations` (op2b forwarding only), `_log_validation_reject`.
NOT in scope: `check_ladder.py`, `workflows/engine.py`, `phases/`, `error_codes.py`, every other workflow file.
Sibling-test audit (§1a) from this list, run with `--require-clean` before freeze: `test_bd141_check_ladder.py`, `test_bd164_preflight.py`, and the 7 other tests that reference `_invoke_validation_llm`: `test_bd141_p4d_role_template_injections.py`, `test_bd92_per_cycle_artifacts.py`, `test_gh705_callsite_stable_prefix.py`, `test_phase_5_implement_A3398552.py`, `test_gh963_validation_execution_failure.py`, `test_llm_subprocess_allowed_tools.py`, `test_7C4D70ED_red_executability_check.py`, `test_phase_5_graphfirst_DA48BEAC.py` (grep at branch point), plus the reject-log tests `test_EECA708D_reject_reason_capture.py`, `test_GH706_validate_cap_directed_reject.py` and every file matching `grep -l 'record_validation_reject\|reject_stats' engine_py/tests`. Baseline at branch point: the first 10 files 238 passed, 1 skipped.
The PR must show zero deleted lines in `workflows/` and `check_ladder.py` (add-only).

## §5 Provenance (Guy 2026-10-03)

Removes or simplifies nothing, so no "introduced:" line is owed. The gate itself: introduced in HAL as
`never_skip_opus_validation_gate`; bd history from da4d41e (2026-07-16); incident provenance pending the lot-assume audit.

## §6 Verification

Scoped: `pytest engine_py/tests/test_bd218_s1_preflight_rung.py` + the §4 siblings; `mypy --strict preflight.py`; full suite on CI (delta, §1r).
Baseline for the cost claim: none made — this step adds an event, no $ change is claimed.

## §7 LLM stages in this step (Guy 2026-10-03: remove LLM wherever a script/test/Jev can do it)

This step adds no LLM call. The one LLM stage it touches is the pre-GREEN validation gate; it is **kept**: the audit verdict (hal#2320) is "Opus stage stays until the reject logs show all its findings are caught by a script". Op3 collects that evidence in shadow. Justification owed in the PR that does replace it: which findings of
the gate (from the reject logs) a script or test already catches, and what stays. This rung is the measuring point: the
`preflight_receipt` event next to the gate's verdict gives, per run, whether the cheap checks were green when the gate ran.
