# bd#141 item 6 (residue): the review-readiness gate inside engine Phase 6

**Status: r3 FROZEN (gate r3 APPROVED; gate r1 REJECTED: 1 MAJOR + 6 MINOR; gate r2 REJECTED: 2 MAJOR in RED + 3 MINOR; all fixed, see `…-gate-r1.md`, `…-gate-r2.md`)** · **Tier:** 2 (two engine prod `.py` edits, `readiness.py` and
`workflows/phase_6_review.py`, plus docs; Option D) ·
**Class:** SYSTEMATIC ·
**Chokepoint:** `readiness.verdict(repo, stage, spec_path)`, which stays the only readiness decision.
This lot adds a third stage, `review`, and the optional policy key `readiness.review_label`. The
engine side is one named helper, `phase_6_review._readiness_review_gate`. It does I/O around the
verdict (resolve the repo, map the verdict to a `StepResult`, emit one event) and decides nothing
itself. It mirrors `phase_5_implement._readiness_start_gate` (PR #160).
**Side of the seam (decision 2026-07-26 §7.1 / §7.4):** engine. HAL adds nothing new.
**Source:** bd#141 item 6 (Warp №5: "review does not start without the ready label"). The start-gate
spec `2026-10-02-bd141-start-gate.md` §4 and bd#117 §1v deferred it to this lot.

## §1 Problem (measured on `5d15316`)

1. bd#141 item 6 has two halves. The first, "implementation does not start without
   `plan-approved`", shipped in PR #160 (`phase_5_implement._readiness_start_gate`). The second,
   "review does not start without the ready label", has no code:
   `grep -n readiness engine_py/bytedigger_engine/workflows/phase_6_review.py` → 0 hits.
2. `readiness.verdict` accepts only `start` and `ship` (`readiness.py:633`). The policy has one
   label (`readiness.label`, default `plan-approved`, `readiness.py:283`). Nothing expresses a second
   human signal of the form "the implementation is ready for review".
3. Engine Phase 6 (`phase_6_review_workflow`, `phase_6_review.py:5921`) starts with
   `build_review_prompt` and then spawns the review fan-out (`invoke_review_llm`). Under
   `readiness.required: true` the reviewers spend tokens on an implementation that no human has
   marked ready.

## §2 Design

### op1 — `readiness.py`: policy key `review_label` and stage `review`

- `_Policy` gains the field `review_label: str | None`. `_load_policy` reads
  `cfg.get("review_label")`. If the key is absent or JSON `null`, the value is `None`. A value that is
  not a non-empty `str`, or that equals `label` under `casefold()`, raises
  `_Unavailable("readiness has fields of the wrong type", policy=True)`, the same as the other field
  errors, so every stage becomes UNAVAILABLE. **Order (r2, m2):** the check runs together with the
  other field checks, *before* `if not required: return None` (`readiness.py:292`), so a malformed
  `review_label` is UNAVAILABLE even under `required: false`.
- `_verdict` accepts `stage in ("start", "review", "ship")`. Any other value raises
  `ValueError` as today, and the message names all three stages.
- At stage `review`:
  - The policy is off (`_load_policy` → `None`), or `pol.review_label is None` ⇒ the result is exactly
    `_default_result()` (verdict `OFF`, `required: False`, `label: "plan-approved"`). No `gh` call is
    made.
  - Otherwise the result has `required: True` and `label = review_label`. Branch → issue binding works
    as today (`no_issue` ⇒ NOT_APPROVED).
  - The decision is the existing `_decide` table evaluated with the policy's `label` replaced by
    `review_label` (`pol._replace(label=pol.review_label)`) and `spec_text=None`. The reasons are
    therefore `no_spec_record` / spec-record reasons, `label_absent`, `label_predates_spec`,
    `approver_not_allowed`, `self_approved`. `spec_changed` never applies, and neither does the ship
    retry branch. `approval_consumed` cannot occur: consumption records name plan-label event ids
    only.
  - Never posts, never consumes, never removes a label.
- Stages `start` and `ship` are byte-for-byte unchanged. They ignore `review_label`, apart from the
  type check above.
- CLI: `check --stage` choices become `start`, `review`, `ship`; the usage comment in
  `scripts/readiness:3` becomes `--stage {start|review|ship}` (r2, m4; comment only). `_report` treats `review` like
  `start`: a policy-read UNAVAILABLE exits 0 (warn-only) and any other UNAVAILABLE exits 4.
  NOT_APPROVED still exits 3 with the line `E_READINESS_NOT_APPROVED <reason> #<N>`.

### op2 — `_readiness_review_gate(ctx, prev) -> StepResult | None` (new helper, `phase_6_review.py`)

- Module-level import `from bytedigger_engine import readiness as _readiness` (the same alias as
  phases 5 and 8; tests monkeypatch `p6._readiness.verdict`).
- `repo, source = resolve_git_cwd_with_source((getattr(ctx, "org_config", None) or {}))`. This is the
  pattern the module already uses at `phase_6_review.py:5249`.
- **Ambient guard (GH1220):** if `is_ambient_git_cwd(source)` the verdict call is skipped. The event
  is emitted with `verdict="UNAVAILABLE"`, `reason="ambient_git_cwd"` and the other values `None`,
  and the helper returns `None`.
- `gate = _readiness.verdict(repo, "review")`. An exception from that call, or from resolving the
  repo, is treated as `{"verdict": "UNAVAILABLE", "reason": "internal error: <repr, one line>",
  "issue": None, "record_sha256": None}`.
- Mapping. It is the same table as the start gate:
  | verdict | helper returns | event |
  |---|---|---|
  | `OFF` | `None` (proceed) | none |
  | `APPROVED` | `None` | `readiness_review_verdict` |
  | `UNAVAILABLE` | `None` (fail-open) | `readiness_review_verdict` |
  | `NOT_APPROVED` | error `StepResult` (below) | `readiness_review_verdict` |
- Error `StepResult`: `status="error"`, `data=None`, `duration_ms=0`,
  `step_name="build_review_prompt"`,
  `error=f"readiness: not ready for review ({reason}) #{issue_tag}"` with
  `issue_tag = "" if issue is None else str(issue)`, `error_code="E_READINESS_NOT_APPROVED"` (no
  new code), `recoverable=False`.
- Event: `_emit_safe("readiness_review_verdict", {"stage": "review", "verdict": ..., "reason": ...,
  "issue": ..., "record_sha256": ...})`. It has exactly these five keys, with values copied from
  `gate`.

**Why fail-open:** the ship gate is the fail-closed layer, as in the start gate. A GitHub outage must
not block local work.

### op3 — call site: first statement of `phase_6_review._build_review_prompt`

`refusal = _readiness_review_gate(ctx, _prev); if refusal is not None: return refusal`. It runs before
the scratchpad reads, before `_review_plan`, and so before any review prompt is written or any
reviewer is spawned. The `steps` list of `phase_6_review_workflow` is **unchanged** (no new
`StepContract`). Phase 4.5's own `_build_review_prompt` functions (`phase_45_spec.py:3982`,
`phase_45_spec_lite.py:543`) are separate functions and are **not** gated. Re-entry (a re-attempt or a
resume that re-runs step 1) re-runs the gate. `review` never consumes, so this is idempotent.

**Terminal path (r2, M1).** A refusal is a terminal phase error of a workflow that declares
`error_handler=_on_phase_6_abort` (`phase_6_review.py:5924`). In production the engine therefore
(a) runs the GH1626 B handler, which writes `reviews/build-satisfaction.md` as a NOT_ASSESSED stub
naming `build_review_prompt` / `E_READINESS_NOT_APPROVED` (a later full Phase 6 run replaces the
stub, per the handler's whitelist), and (b) because `recoverable=False`, emits the dispatcher report
and a `terminal_failure` stuck-report (`engine.py:407-430`). This is intended: Phase 7 must not read
the missing review as silence. No review prompt, role file or review doc is written.

### op4 — registries and docs

- `error_codes.py`: the `E_READINESS_NOT_APPROVED` description is rewritten so it names both labels
  (r2, m6): the bound issue has no current `plan-approved` approval (refused by the
  `phase_5_implement` start gate or the `phase_8_post_deploy` ship gate) **or** no current review
  label (refused by the `phase_6_review` review gate, bd#141 item 6). Regenerate both `ERROR_CODES.md` from `render_markdown()`.
- `docs/configuration.md` readiness section: add the row `readiness.review_label` | string | absent
  (off) | "the label a human adds to mark the implementation ready for review; when set, engine Phase 6
  refuses review without it". Extend `check --stage start|ship` to `start|review|ship`. Add one bullet
  covering the Phase 6 gate, its fail-open behaviour, and recovery: a human adds the review label,
  then resume Phase 6.
- `CHANGELOG.md` `[Unreleased]` → `### Added`: one bullet naming `bd#141 item 6` and `review_label`.

## §3 Acceptance criteria (RED: `engine_py/tests/test_bd141_p6_review_gate.py`)

Rig: reuse `make_rig` and the record helpers from `test_bd117a_readiness.py` (import them, do not
copy them): a real git remote and a stateful fake `gh` via `HAL_GH_BIN`. The policy is
`{"readiness": {"required": true, "review_label": "ready-for-review"}}` unless an AC says otherwise.
The branch is `gh42-x`. `org_config` carries `git_cwd=<rig.repo>` and `scratchpad_dir=<rig scratch>`.
Events are captured by monkeypatching `p6._emit_safe`.

**readiness.py**
- **R1 (review OFF without key)** required policy without `review_label`: `verdict(repo, "review")`
  equals `_default_result()`; zero fake-gh calls.
- **R1b (OFF before issue binding, r3 F5)** required policy without `review_label`, branch
  `feature-x`: `verdict(repo, "review")` equals `_default_result()` (not `no_issue`). (Reddens if
  the `review_label is None` short-circuit sits after `_bind`'s issue binding.)
- **R2 (review OFF when not required)** `required: false` plus `review_label`: verdict `OFF`; zero
  fake-gh calls.
- **R3 (APPROVED)** record posted, `plan-approved` added, then `ready-for-review` added after the
  record: `verdict="APPROVED"`, `label="ready-for-review"`, `issue=42`, `required=True`. The fake-gh
  log has no comment create and no label removal.
- **R3b (plan consumption does not leak, r3 F5)** R3's rig plus a consumption record of the plan
  label event for another branch: `review` is still `APPROVED`.
- **R4 (only plan label)** record and `plan-approved` present, no ready label: `NOT_APPROVED`,
  `reason="label_absent"`. (Reddens if `review` reuses `pol.label`.)
- **R5 (ready predates record)** ready label added, then a new record posted, label still present:
  `reason="label_predates_spec"`.
- **R6 (self-marked ready)** `distinct_actor: true`, ready label added by the BD user:
  `reason="self_approved"`.
- **R7 (approvers)** `approvers: ["alice"]`, ready added by `bob`: `reason="approver_not_allowed"`.
- **R8 (no issue)** branch `feature-x`: `NOT_APPROVED`, `reason="no_issue"`, `issue=None`.
- **R9 (policy type errors)** `review_label` is `""`, `5`, or `"Plan-Approved"` (equal to `label`
  under casefold): the verdict at **each** of `start`, `review`, `ship` is `UNAVAILABLE`, with reason
  containing `wrong type`. Also with `required: false` and `review_label: 5`: `review` → `UNAVAILABLE`
  (r2, m2).
- **R9b (explicit null, r3 F5)** required policy with `"review_label": null`: `review` → `OFF`
  (equals `_default_result()`), `start` is not UNAVAILABLE.
- **R10 (start/ship unchanged)** R4's rig (plan label only, no ready label): `verdict(repo, "start")`
  is `APPROVED`. A rig with a ready label but no plan label gives `start` → `label_absent`. (Reddens
  if `start` reads `review_label`.)
- **R11 (stage validation)** `verdict(repo, "deploy")` raises `ValueError` whose message contains
  `review`.
- **R12 (CLI)** `main(["check", "--stage", "review", "--repo", rig.repo])`: 3 on R4's rig with stderr
  `E_READINESS_NOT_APPROVED label_absent #42`, and 0 on R3's rig. On a policy whose `readiness` is
  not an object (policy-read UNAVAILABLE) it returns 0 at `review`, and the same call at `ship`
  returns 4.

**phase_6_review.py**
- **R13 (structure)** `[s.name for s in phase_6_review_workflow().steps]` equals the list pinned on
  `5d15316`. No step name contains `readiness`.
- **R14 (helper OFF)** a policy without `review_label`: the helper returns `None` and no
  `readiness_review_verdict` event is emitted.
- **R15 (helper refuses)** R4's rig: the result has `status="error"`,
  `error_code="E_READINESS_NOT_APPROVED"`, `recoverable is False`,
  `error == "readiness: not ready for review (label_absent) #42"` and
  `step_name == "build_review_prompt"`. One event has `stage="review"`, `verdict="NOT_APPROVED"`,
  `reason="label_absent"` and keys exactly the five of op2.
- **R16 (helper approves)** R3's rig: `None`, plus one event with `verdict="APPROVED"`. Two calls
  give two `None`; the fake-gh log has no comment create and no label removal.
- **R17 (fail-open)** (a) fake gh exits non-zero on the issue read ⇒ `None` and an event with
  `verdict="UNAVAILABLE"`. (b) `p6._readiness.verdict` raises `RuntimeError("boom")` ⇒ `None`, and
  the event reason starts with `"internal error"` and contains `boom`. (c)
  `p6.resolve_git_cwd_with_source` raises `RuntimeError("cwdboom")` ⇒ `None`, the reason contains
  `cwdboom`, and the verdict spy is not called.
- **R18 (arguments)** a spy on `p6._readiness.verdict` is called exactly once per helper call, with
  positional `(repo, "review")` where `Path(repo).resolve() == rig.repo.resolve()`. It is never called
  with `"start"` or `"ship"`.
- **R19 (ambient skipped, GH1220)** `org_config` without `git_cwd` / `current_worktree_path`, and
  `monkeypatch.chdir(rig.repo)` ⇒ `None`. The verdict spy is not called. There is one event with
  `verdict="UNAVAILABLE"`, `reason="ambient_git_cwd"`. `git -C rig.repo show-ref refs/bd/policy`
  finds nothing, and there are zero fake-gh calls.
- **R20 (production side-effect, §1l)** run the real `phase_6_review._build_review_prompt(ctx, None)`
  on R4's rig, with `p6._review_plan` replaced by a recorder that raises a sentinel. The test catches
  the sentinel so a pre-GREEN RED fails at the assert (§1q). Then: `error_code` is
  `E_READINESS_NOT_APPROVED`, and the recorder saw **zero** calls (r2, M1: the always-true
  `reviews/` emptiness assertion is dropped; `_review_plan` not being reached is the signal).
- **R21 (passes through when OFF)** R20 on an OFF rig: the recorder sees exactly one call. This
  proves the gate does not swallow the step.

**Registries and docs**
- **R22** the `error_codes` description of `E_READINESS_NOT_APPROVED` contains `phase_6_review`.
  Both `ERROR_CODES.md` `E_READINESS` sections contain `phase_6_review`.
- **R23** the readiness section of `docs/configuration.md` contains `review_label`,
  `start|review|ship` and `phase_6_review`. The `[Unreleased]` section of `CHANGELOG.md` contains
  `bd#141 item 6` and `review_label`. `scripts/readiness` contains `{start|review|ship}`. The
  `E_READINESS_NOT_APPROVED` description contains `review label`.
- **R24 (abort-handler path, M1)** on R4's rig, take the refusal `StepResult` from the real
  `_build_review_prompt` and pass it to `p6._on_phase_6_abort(result, ctx)`:
  `<scratch>/reviews/build-satisfaction.md` exists and contains `E_READINESS_NOT_APPROVED` and
  `build_review_prompt`. (Reddens if the refusal carries another code or step name.)

Each negative AC names the change that reddens it:
- R1/R2/R14 ⇐ gating without a `review_label`.
- R4 ⇐ checking the plan label.
- R5 ⇐ not comparing against the record time.
- R6/R7 ⇐ dropping the approver rules.
- R9 ⇐ no type check, or the check placed after `required`.
- R10 ⇐ `start` reading `review_label`.
- R12 ⇐ the CLI choices not extended, or `review` fail-closed on a policy error.
- R13 ⇐ adding a `StepContract`.
- R15 ⇐ wrong format, wrong step name, or `recoverable=True`.
- R17 ⇐ fail-closed.
- R18 ⇐ the wrong stage.
- R19 ⇐ no ambient guard.
- R20 ⇐ the gate placed after `_review_plan`, or not wired.
- R21 ⇐ the gate always refusing.
- R24 ⇐ a refusal with another `error_code` or `step_name`.

## §4 Declared limits

- The gate is advisory against the model, as in bd#117. It stops an engine build before the Phase 6
  reviewers spawn. It does not stop a host-driven review or a direct `git push`.
- The ready label is **not consumed**, so it can outlive a build. The record-time check (R5) resets it
  when a new spec is posted for the same issue, but a second build on the same record reuses a stale
  ready label. A human removes the label when it should no longer count.
- The gate runs on every Phase 6 entry, including resumes: step sentinels are per step
  (`step_sentinel.py:139-163`) and step 1 is re-run on every execute (r2, m1). A resume whose review
  is already cached still refuses if the ready label was removed or a newer record was posted. Only the
  in-phase satisfaction fix loop (`phase_6_review.py:3699`) re-enters after step 1, after a gate that
  already passed.
- A malformed `review_label` makes every stage UNAVAILABLE, so `check --stage ship` and `readiness post`
  exit 4 (fail closed) even if review is never used (r2, m2; r3, F4). This matches every other readiness field error.
- The bd#85 task driver does not list `E_READINESS_NOT_APPROVED` in `_STOP_CODES`
  (`lib/task_resume.py:60`), so `plan_resume` re-runs a refused Phase 6 until the run cap
  (`DEFAULT_MAX_RUNS = 3`) is spent. Each re-run refuses again at the gate before any reviewer
  spawns, so the cost is the policy read only. This is inherited from the start gate; adding the code
  to `_STOP_CODES` changes both gates and is a follow-up, not this lot (r2, m6).
- Every Phase 6 step-1 entry with a non-ambient git_cwd and any `origin` does the policy read
  (`ls-remote` plus one fetch into `refs/bd/policy`), as Phases 5 and 8 already do. No origin means no
  network. No latency claim is made.
- No `awaiting_review` state in the engine. A refusal is a non-recoverable phase failure, and the
  build resumes after a human adds the label.
- HAL adapters and host-controls entries for item 6 are out of scope (window 1955 owns HAL adapters).

## §5 Scope

In: `engine_py/bytedigger_engine/readiness.py`, `engine_py/bytedigger_engine/workflows/phase_6_review.py`,
`engine_py/bytedigger_engine/error_codes.py`, `engine_py/ERROR_CODES.md`,
`engine_py/bytedigger_engine/ERROR_CODES.md`, `docs/configuration.md`, `CHANGELOG.md`,
`scripts/readiness` (usage comment line 3 only), `engine_py/tests/test_bd141_p6_review_gate.py`
(new), and this spec.

### Files NOT in scope (§1v)
`phase_5_implement.py`, `phase_8_post_deploy.py`, `phase_45_spec*.py`, `lib/task_resume.py`,
`scripts/ship.sh`, prompt files (`phases/*.md`, `commands/build.md`, `skills/bytedigger/SKILL.md`),
`companion_tune.py`, `core_manifest.json`, `mypy-strict-modules.txt`, and HAL's tree.

### Sibling tests (§1a): must stay green
Exact list on `5d15316` (r2, m3; 39 files: every test file mentioning `phase_6_review` together with `_build_review_prompt` or `phase_6_review_workflow`, plus the readiness siblings): `test_291189a0_phase6_step_factory.py`, `test_906e37dc_review_findings_audit.py`, `test_bd117a_readiness.py`, `test_bd119_role_template.py`, `test_bd139_single_reviewer.py`, `test_bd141_p4d_role_template_injections.py`, `test_bd141_p5_revert_signal.py`, `test_bd141_p6_start_gate.py`, `test_bd85_retry_budgets.py`, `test_bd86_fact_pack.py`, `test_F7830037_insession_review_normalize.py`, `test_F9F7E4FD_out_of_role_injection.py`, `test_fix_watchdog.py`, `test_gh1591_fix_gate_boundary.py`, `test_gh1626b_satisfaction_on_abort.py`, `test_gh268_test_only_autodetect.py`, `test_gh379_decorr_verify.py`, `test_gh497_telemetry_hygiene.py`, `test_gh557_resume_seam_flips.py`, `test_gh705_callsite_stable_prefix.py`, `test_gh751_satisfaction_spec_anchor.py`, `test_phase_5_b4d83b40_red_rubric.py`, `test_phase_6_build_scope_prompt.py`, `test_phase_6_commit_fix_tests_8FE3D757.py`, `test_phase_6_last_findings_persist_c834481a.py`, `test_phase_6_mass_unverified_5F9817F6.py`, `test_phase_6_post_fix_pytest_gate_7A940850.py`, `test_phase_6_post_fix_typecheck_gate_GH316.py`, `test_phase_6_postfix_and_polish_3F5599A6.py`, `test_phase_6_review_21792EE7.py`, `test_phase_6_review_E52F241F.py`, `test_phase_6_review_return_discipline_CF838E6F.py`, `test_phase_6_review_simple_fastpath.py`, `test_phase_6_review_W2.py`, `test_phase_6_rubric_trim_5D0D3BD1.py`, `test_phase_6_step10_commit_fix_code.py`, `test_phase_6_step7_w1_disk_truth.py`, `test_phase_6_subagent_prior_context_propagation_7ca211d2.py`, `test_phase_6_test_only_note_FF2CB91D.py`. Local baseline before RED: 831 passed, 1 skipped.
Most of them
set no `git_cwd` (ambient ⇒ skip) or point it at a repo without `origin` (policy off). The full suite
plus the delta against the RED baseline is the ship gate (§1r).
