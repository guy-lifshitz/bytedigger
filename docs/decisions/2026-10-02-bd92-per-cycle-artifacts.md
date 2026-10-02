---
red_tests:
  - engine_py/tests/test_bd92_per_cycle_artifacts.py
sibling_tests:
  - engine_py/tests/test_gh636_findings_thread_evict.py
  - engine_py/tests/test_bd85_retry_budgets.py
  - engine_py/tests/test_bd150_class_i_inventory.py
  - engine_py/tests/test_resume_key_lint_00321AB4.py
red_pins:
  - engine_py/tests/test_gh636_findings_thread_evict.py
  - engine_py/tests/test_bd85_retry_budgets.py
paths:
  - engine_py/bytedigger_engine/findings_sidecar.py
  - engine_py/bytedigger_engine/workflows/phase_45_spec.py
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - engine_py/bytedigger_engine/lib/resume_keying.py
  - engine_py/bytedigger_engine/lib/step_sentinel.py
  - engine_py/bytedigger_engine/engine.py
---
# bd#92 — per-cycle artifacts are cleared or keyed before a later cycle can read them

**Status:** DRAFT r2 (gate r1 REJECT: M1, M2, m1–m8 addressed; see `2026-10-02-bd92-gate-r1.md`).
**Tier:** OPTION_D (engine_py prod `.py`).
**Class:** SYSTEMATIC. **Chokepoint:** each per-cycle artifact has exactly one
owner step. The owner either unlinks the artifact before the step that
produces it runs (unlink-before-invoke, the bd#139 pattern at
`phase_6_review.py:1054` and `phase_45_spec.py:1300`), or every reader
rejects the artifact unless its recorded key (run_id, cycle, verdict) matches
the current one. For sentinels the chokepoint is the single name/glob builder
in `lib/resume_keying.py`.
**Side of the seam:** engine core. No host or provider code is touched, and
nothing depends on subscription vs API tokens.
**Source:** HAL engine_py @456d46e97 evidence in bd#92, mapped onto bytedigger
@56fbbfa.

## §1 Problem (measured on 56fbbfa)

| # | HAL item | bytedigger state |
|---|---|---|
| 1 | per-role review files globbed into aggregation, never unlinked | The glob is gone (bd#139/bd#89). `reviews/role-composite.md` is unlinked before every reviewer invoke (`phase_6_review.py:1054-1061`). **But** `reviews/build-review.md` (`REVIEW_DOC_RELPATH`, :249) is never unlinked. `_resolve_review_content` (:1975-1989) prefers a non-empty on-disk doc over stdout. A cycle where the reviewer writes no composite and no doc therefore silently reuses the previous cycle's review. |
| 2 | spec-phase review fallback reuses an old doc | Not present. `_write_review_doc` (`phase_45_spec.py:4306`) writes only `raw_response`; review docs are per-cycle paths (:361). Out of scope. |
| 3 | findings thread not keyed by run/cycle | Present. `findings_sidecar.py` writes `{structured_findings, cycle}` to `<scratchpad>/.findings-thread.json`. `load_findings_thread` ignores `cycle` and there is no `run_id`. `_build_spec_prompt` loads it on any cycle ≥ 2 when `_prev` has no thread (`phase_45_spec.py:1051-1052`). A previous run's thread, or a thread from cycle N-2, is applied as if it were cycle N-1's. |
| 4 | "shipped" sidecar written on lint pass | Present. `_write_ship_sidecar` is called on spec-lint `rc == 0` (`phase_45_spec.py:1957`), not on the reviewer's SHIP. `_prior_ship_base_inline` (:981) injects any sha-matching spec as "PRIOR SHIP-SPEC BASE (surgical revise mode)", including a spec the reviewer then sent back with REVISE. Nothing ever unlinks it. |
| 5 | sentinel invalidation glob misses real names | Prefix is correct. Two real gaps: **(a)** `resume_sentinel_name` maps `run_id` None/"" to `norun` (`lib/resume_keying.py:29`), but both glob builders interpolate the raw value (`step_sentinel.py:200`, `:250-251` → `_rNone_h*`), so hashed/validation sentinels with no run id are never cleared. **(b)** The phase-reroute entry (`engine.py:327`) clears only **cycle 1** sentinels. A restarted attempt (same run_id) that reaches cycle 2 replays the previous attempt's stale cycle-2 sentinels and skips the work they claim. |

## §2 Design

### op1 — phase 6 review doc is unlinked before the reviewer runs
In `_invoke_review_llm` (`phase_6_review.py`), the existing bd#139 guard also
unlinks `Path(prev.data["doc_path"])` (the path the reader `_write_review_artifact`
uses; it equals `scratchpad / REVIEW_DOC_RELPATH`, which phase 7 also reads at
`phase_7_synthesize.py:433`, so one unlink covers both readers), in the same try/except OSError
(fail-safe, never fails the step). No other change to phase 6. The fix,
satisfaction, and post-fix artifacts are out of scope; they already have their
own unlinks or are not read back as a fallback.

### op2 — findings thread keyed by (run_id, producing cycle)
`findings_sidecar.py`:
- `persist_findings_thread(scratchpad, sf, *, cycle, run_id)` writes
  `{"structured_findings", "cycle", "run_id"}`. `run_id` may be None.
- `load_findings_thread(scratchpad, *, run_id, for_cycle)` returns the list
  only when stored `run_id == run_id` and stored `cycle == for_cycle - 1`.
  Otherwise it returns None. A legacy payload without a `run_id` key also
  returns None. It degrades and never raises.
- `findings_thread_status(scratchpad, *, run_id, for_cycle) -> tuple[list | None, str | None]`
  is the single reader (new, in `findings_sidecar.py`, so no new raw read
  appears in `workflows/`). It returns `(list, None)` or `(None, reason)`,
  `reason` ∈ {`absent`, `malformed`, `legacy`, `run_mismatch`,
  `cycle_mismatch`}. Precedence: unreadable/absent → `absent`; payload not a
  dict, `structured_findings` not a non-empty list, or `cycle` not a real
  `int` (bool rejected) → `malformed`; no `run_id` key → `legacy`; run_id
  differs → `run_mismatch`; cycle ≠ `for_cycle - 1` → `cycle_mismatch`.
  `load_findings_thread` is `findings_thread_status(...)[0]` and never raises.
- **Attempt boundary (gate M2).** A restarted (rerouted) attempt keeps the
  run_id, and a REVISE that produces no findings persists nothing, so the key
  alone cannot tell attempts apart. The owner step is cycle-1 prompt build:
  `_build_spec_prompt` at `cycle == 1` unlinks the thread sidecar (fail-safe,
  OSError logged) before it does anything else. Every attempt starts at
  cycle 1, so attempt 2 cannot read attempt 1's thread. Crash-resume inside
  cycle ≥ 2 does not unlink.
- Callers: the `_gate_on_review` REVISE branch (:4881) passes the current
  run id (`telemetry_ctx.get_current_run()`, None if absent).
  `_build_spec_prompt` (:1052) passes the current run id and
  `for_cycle=cycle`. The `gate_retry` exclusion (bd#85) is unchanged.
- When the status reason is `legacy`, `run_mismatch`, `cycle_mismatch` or
  `malformed`, `_build_spec_prompt` emits `spec_findings_thread_rejected`
  `{reason, cycle}` through `_emit_safe`. `absent` emits nothing.

### op3 — the ship sidecar means "the reviewer said SHIP"
- Remove the `_write_ship_sidecar` call from the spec-lint `rc == 0` branch.
- `_finalize_ship_verdict` calls `_write_ship_sidecar(prev.data["spec_path"])`.
  The sidecar gains `"verdict": "SHIP"`. It stays fail-open.
- **Single site (gate M1).** `_gate_on_review` unlinks the ship sidecar
  (fail-safe) immediately after the SHIP branch returns (after `:4596`), i.e.
  before any non-SHIP branch: UNKNOWN/empty, infrastructure block, the
  exhausted-counter terminal (:4738-4761), the frozen-fallback retry
  (:4768-4790), the cap-reached terminal and the engine-retry REVISE. A spec
  the reviewer sent back is never a "prior shipped base". This matters
  because op3 makes a frozen-spec SHIP write the sidecar (lint is skipped for
  frozen specs, :1871), and a frozen copy reproduces the same sha.
- `_prior_ship_base_inline` additionally requires `sidecar.get("verdict") ==
  "SHIP"`. Order: file/sidecar absent → existing `spec_prior_base_spec_missing`; then the
  verdict check (`spec_prior_base_unverified`, returns `("", [])`); then the
  existing sha check (`spec_prior_base_stale`). Legacy lint-written sidecars are ignored, not trusted.

### op4 — one sentinel name/glob source; reroute clears every cycle
- `lib/resume_keying.py` adds `resume_sentinel_glob(step_name, cycle,
  run_id, workflow_name=None, *, hashed: bool) -> str`. `cycle` is an int or
  the literal `"*"`. It lives in `lib/resume_keying.py` and builds the name by calling
  `resume_sentinel_name` with a placeholder cycle/hash and substituting the
  wildcard, so no inline `_done_c…json` f-string is added there (the
  `resume_key_lint` sibling `test_resume_key_lint_00321AB4.py` and mypy strict
  must stay clean). It applies the same `run_id or _NO_RUN` normalization
  and prefix as `resume_sentinel_name`. For every input, a name built by
  `resume_sentinel_name` matches the glob built from the same arguments
  (fnmatch). The R1/R12 anchoring (GH897 r2) is preserved.
- `invalidate_cycle_sentinels` and `invalidate_validation_sentinels_for_run`
  build every pattern through it. No inline f-string glob remains in
  `step_sentinel.py`.
- `invalidate_cycle_sentinels(..., cycle=None)` means all cycles; its
  `step_sentinel_invalidated` event then carries `cycle: "all"`: legacy and
  hashed names are both matched with `"*"`.
- `engine.py` reroute entry (:327) calls it with `cycle=None`. A restarted
  attempt starts with no sentinel from any cycle of the previous attempt. The
  one-shot mark-then-invalidate guard is unchanged, so a crash-restart still
  replays the current attempt's sentinels (resume keeps working).

## §3 Non-goals / files NOT in scope (§1v)
- `workflows/phase_5_implement.py`, including the validation-loop reroute at
  :8082 (cycle-1 only). This is the pre-GREEN gate area of #91 (lot-1570).
  It is a follow-up comment on bd#92; this lot does not touch it.
- Ordinary durable resume within one attempt keeps replaying sentinels. That
  is the purpose of sentinels; they are keyed by run_id + ctx hash.
- Phase 4.5 review doc (item 2): not present.
- PR #210 (bd#94) touches other hunks of `phase_6_review.py` (imports,
  `_autocommit_fix_tail`, test-commit `git add`). There is no overlap with
  `_invoke_review_llm`.
- #206/#192 (class-M declarations), #211 (version guard), #89 phases/ docs.

## §4 Inventory (bd#150)
`unlink`/`glob` are not scanned callees. No new `read_text`/`json.load`/
subprocess site is added: the ship sidecar read stays in `_read_ship_sidecar`,
and `load_findings_thread` lives outside `workflows/`/`lib/`. If a GREEN edit
shifts an ordinal or qualname in `phase_45_spec.py`/`phase_6_review.py`/
`lib/step_sentinel.py`, the key is updated in
`conformance/class_i_inventory.json`. `test_bd150_class_i_inventory.py` must
stay green. Rebase on main before the PR. If #210 has merged by then, the
tree_scan inventory is checked too.

## §5 Scope (files GREEN may change)
`engine_py/bytedigger_engine/findings_sidecar.py`,
`workflows/phase_45_spec.py`, `workflows/phase_6_review.py`
(`_invoke_review_llm` only; phase 7 is read-only here), `lib/resume_keying.py`, `lib/step_sentinel.py`,
`engine.py` (reroute block only), `conformance/class_i_inventory.json`
(re-key only), `engine_py/ERROR_CODES.md`/`docs/events.md` (new event names
only, if those docs list events).

## §6 Acceptance criteria
- **AC1** (op1, side-effect): with a non-empty stale `reviews/build-review.md`
  on disk and a stubbed reviewer that writes neither composite nor doc but
  prints fresh stdout, after `_invoke_review_llm` the stale file is gone from
  disk, and `_write_review_artifact` writes the fresh stdout content, not the
  stale text.
- **AC2** (op1): an `OSError` from the unlink does not fail `_invoke_review_llm`.
- **AC3** (op2): `load_findings_thread` returns None for a sidecar from
  another run_id, for a stored cycle ≠ `for_cycle - 1`, and for a legacy
  payload without `run_id`. It returns the list for a same-run, previous-cycle
  sidecar (positive control, the GH636 recovery still works).
- **AC4** (op2, prompt side-effect): `_build_spec_prompt` at cycle 2 with
  empty `_prev` thread and a sidecar from a different run does not put that
  sidecar's findings into the prompt, and emits `spec_findings_thread_rejected`.
  The same setup with a same-run cycle-1 sidecar does include them.
- **AC5** (op2, regression lock, no new test): `test_bd85_retry_budgets.py::test_ac7` (updated in this lot) persists a same-run cycle-2 thread and asserts a gate retry's prompt uses the gate findings. It is red until op2 lands (new `run_id` kwarg) and then locks the bd#85 exclusion. It is a pinned sibling, not a RED-file test, because a RED file may not contain a passing test.
- **AC6** (op2): the REVISE branch of `_gate_on_review` persists `run_id` and
  `cycle` into the sidecar on disk.
- **AC7** (op3): spec-lint `rc == 0` does not create `.build-spec.ship.json`.
- **AC8** (op3, side-effect): a `_gate_on_review` SHIP verdict writes the
  sidecar with `verdict == "SHIP"`, the sha256 of the on-disk spec, and the
  run_id.
- **AC9** (op3): REVISE-retry, cap-reached terminal, frozen-fallback retry and exhausted-counter terminal verdicts each unlink an existing ship sidecar (one test per branch, driving the real `_gate_on_review`).
- **AC10** (op3): `_prior_ship_base_inline` with a sha-matching sidecar that
  has no `verdict` key (legacy lint-written) returns `("", [])` and emits
  `spec_prior_base_unverified`. With `verdict == "SHIP"` it returns the block.
- **AC11** (op4): for a matrix of run_id ∈ {uuid hex, None, ""}, workflow_name
  ∈ {None, "phase_45_spec"}, ctx hash ∈ {None, 12+ hex}, and cycle ∈ {1, 2},
  `fnmatch(resume_sentinel_name(...), resume_sentinel_glob(..., hashed=…))` is
  true for both the exact cycle and `"*"`. A run_id `"R1"` glob does not match
  an `"R12"` name.
- **AC12** (op4, side-effect): `invalidate_cycle_sentinels` with `run_id=None`
  removes an on-disk `…_rnorun_h<hash>.json` for an input-hashed step, and
  `invalidate_validation_sentinels_for_run(sp, None)` removes `…_rnorun…` files.
- **AC13** (op4, side-effect): `invalidate_cycle_sentinels(..., cycle=None)`
  removes cycle-1, cycle-2 and cycle-3 sentinels (legacy and hashed) for the
  run and workflow, and leaves another run's and another workflow's untouched.
- **AC14** (op4, restart): an engine `execute` with an unconsumed
  `phase_reroute` and a stale cycle-2 sentinel of the same run on disk
  deletes that sentinel before the first step runs (`phase_reroute_entry`
  `sentinels_invalidated` counts it).
- **AC15** (op2, attempt boundary): with a same-run cycle-1 thread on disk,
  `_build_spec_prompt` at `cycle == 1` leaves no `.findings-thread.json`, and
  a following cycle-2 prompt (empty `_prev` thread) carries none of its
  findings. A cycle-2 prompt does not unlink an existing same-run cycle-1
  thread (positive control).
- **AC16** (op3/op4 hygiene): a source scan asserts no `_done_c` literal in
  `lib/step_sentinel.py`, and `test_resume_key_lint_00321AB4.py` stays green.
- **AC17** (op2): `findings_thread_status` returns reason `malformed` for a
  list payload, a bool `cycle`, and an empty `structured_findings`, and
  never raises.
- **AC18** (§1r): the full engine_py suite has no new reds versus the 56fbbfa
  baseline (CI full-suite on the PR head).
