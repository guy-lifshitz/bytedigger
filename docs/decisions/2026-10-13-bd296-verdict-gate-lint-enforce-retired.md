---
red_tests:
  - engine_py/tests/test_bd296_verdict_gate_lint_permanent_warn.py
red_pins:
  - tests/test_bd89_p3d_flag_owner_lint.py
  - engine_py/tests/test_bd65_window_d_port.py
sibling_tests:
  - engine_py/tests/test_bd_flip_horizon_guard.py
  - tests/test_bd89_p3d_flag_owner_lint.py
  - engine_py/tests/test_bd65_window_d_port.py
  - engine_py/tests/test_bd290_flag_unregistered_warn.py
  - engine_py/tests/test_GH1674_injection_missing.py
  - engine_py/tests/test_bd91_pregreen_strict_and.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_5_implement.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - engine_py/bytedigger_engine/error_codes.py
  - engine_py/ERROR_CODES.md
  - engine_py/bytedigger_engine/ERROR_CODES.md
  - scripts/flip_horizon_ledger.json
  - CHANGELOG.md
---
# bd#296: the verdict-gate lint is a permanent warning; HAL_VERDICT_GATE_LINT_ENFORCE is retired

Slice of #89. Refs #296, #89, #290. Base: origin/main @ 05fe8ca. Measured 2026-10-13.

## 1. Problem

`HAL_VERDICT_GATE_LINT_ENFORCE` (default off) guards one branch in `_gate_on_validation`
(`workflows/phase_5_implement.py`): when the deterministic verdict-gate lint contradicts a verdict
that passed, the step stops with `E_VERDICT_GATE_LINT` only if the flag is set. The catalog entry
carries `flip-by:2026-08-01`, and the ledger extends it to 2026-11-03 with the reason "the warn path
emits no event (0 of 805 runs)". Nobody set the flag and nobody could judge it.

## 2. Measurement (replay of the lint over every verdict file that could be found)

Tool: `bytedigger_engine.verdict_gate.run_gate(doc, repo_root, {})`, unchanged, run over files found
under `~/`, `/private/tmp` and `/Volumes` (Time Machine copies).

### 2.1 The phase 5 seam: validation docs of real runs

| Source | Unique files (by sha256) | Contain the token `VERDICT: APPROVED` | `Verdict: PASS` | `Verdict: FAIL` | Lint result |
|---|---|---|---|---|---|
| `~/.claude/SHARED/state/build-runs` | 397 | 0 | 318 | 78 | 397 NOT_APPROVED |
| other run dirs (`.hal-build/scratchpad`, ppba, lot evidence) | 23 | 0 | 15 | 8 | 23 NOT_APPROVED |
| `SHARED/experiments/opus-47-shadow` | 5 | 0 | 2 | 0 | 5 NOT_APPROVED |
| **Total** | **425** | **0** | | | **0 APPROVED, 0 contradictions** |

(2301 path hits collapse to 425 unique files: three Time Machine snapshots and several worktrees hold
copies. 1 build-runs file and 3 shadow files carry no PASS/FAIL marker.)

Reading: the phase 5 validator is told to end with `Verdict: PASS` or `Verdict: FAIL`
(`phase_5_implement.py`, validation prompt); the lint's approval token is `VERDICT: APPROVED`
(`audit_gate.APPROVAL_TOKEN`, the spec/RED gate-doc format). So at this seam `run_gate` returns
`NOT_APPROVED` for every real validation doc, the `gate_rc != 0` branch is never taken, and neither
the warn event nor the stop can fire. That, not a missing emit, is why 805 runs produced no
`verdict_gate_lint_warn`. A replay of this seam can say nothing about "lint right or verdict right":
there are no APPROVED verdicts to contradict.

### 2.2 The format the lint was written for: APPROVED gate docs

Corpus: `docs/decisions/*.md` of this repo, replayed with `repo_root` = this tree at HEAD.
327 files, 83 contain `VERDICT: APPROVED`.

| Lint result | Docs | Meaning |
|---|---|---|
| GATE_PASS | 13 | lint agrees |
| UNANCHORED | 62 | no `spec:`/`red:` anchor block in the doc |
| DRIFT | 6 | an anchored file's sha256 differs from the file on disk now |
| NO_CHECKLIST | 2 | anchor present, no AC checklist table |
| FAIL_ROW | 0 | a checklist row says FAIL |
| PARITY_* | 0 | spec AC ids and checklist rows disagree |

Contradictions: 70 of 83 (84 %). Who is right:

- UNANCHORED, 62: 58 are dated 2026-10-01..2026-10-03, before the anchor/checklist block existed in
  gate docs; 4 (bd248, bd130, bd277, bd285, 2026-10-10..11) have a checklist and no anchor. The lint is
  literally right that the block is missing; it does not refute the verdict. 0 of 62 show a failing
  row or an AC gap.
- DRIFT, 6 (bd129 r1, bd129 r3, bd192 r1, bd83 r3, bd105 r2, bd125 r2): the anchored file changed
  after the verdict. For bd192 r1 and bd105 r2 a historical revision of the anchored file in `main`
  has exactly the embedded sha, so the doc was correct when written and later lots edited the file.
  For the other four no revision in `main` matches (the revisions lived on squash-merged lot
  branches), so who was right at the time cannot be established from this checkout.
- NO_CHECKLIST, 2 (bd102 r2, bd243 r4): the doc is anchored and its spec file matches on disk; it has
  no checklist table. Format gap, verdict not refuted.

Replay at HEAD is biased towards DRIFT, the real seams run it right after the verdict. That bias does
not help the stop: in this corpus the lint's contradictions are absent or stale evidence, and no
contradiction found is a case where the verdict was shown wrong. Stopping on them would have
blocked 70 of 83 approvals.

### 2.3 What the code does today (checked, not assumed)

A real `_gate_on_validation` call on a doc with `VERDICT: APPROVED` and no anchor, inside a run
context, no `HAL_VERDICT_GATE_LINT_ENFORCE`: status `ok`, event log holds
`verdict_gate_lint_warn {"doc", "result": "UNANCHORED", "rc": 1, "cycle", "phase": 5}`. So the
issue's "the warn branch writes no event" is not true of this tree: the `_emit_safe` call exists and
nothing in this repo pins it. With `HAL_VERDICT_GATE_LINT=0` or with a `Verdict: PASS` doc: no event.

### 2.4 Deadline

`python3 scripts/flip_horizon.py --check --today 2026-11-03` exits 0; `--today 2026-11-04` exits 1 and
names 12 flags, `HAL_VERDICT_GATE_LINT_ENFORCE` among them (ledger `until 2026-11-03`). First red day:
2026-11-04. This lot removes only its own flag; the other 11 entries keep the guard red from the same
day, and their slices are separate.

## 3. Decision

Outcome B of the issue (the lint is not fit to stop): the contradiction is a permanent warning that
writes an event on every occurrence; the flag and the stop branch are deleted. The ledger date is
not moved.

Why not A (stop by default): 70 of 83 approvals in the only APPROVED corpus would be blocked, none
of them shown wrong. Why not "fix the seam so it sees PASS docs": a validator doc has no anchor
block, so every run would warn `UNANCHORED`; that is a change to what the lint checks and to the
validator prompt, both out of scope. It is recorded as a follow-up finding in the PR (the seam is
dormant by construction).

`E_VERDICT_GATE_LINT` is raised in exactly one place in this repo, the branch being deleted. The
host-side audit-gate hook sets its own block flag and never raises the code (read:
`engine-py-audit-gate.py`, outside this repo). The code is removed from the registry.

## 4. Change

1. `workflows/phase_5_implement.py`, `_gate_on_validation`: the lint block keeps the
   `HAL_VERDICT_GATE_LINT` gate, the `run_gate` call, the driver-error fail-open and the
   `_emit_safe("verdict_gate_lint_warn", ...)` call with its payload unchanged. The
   `get_config().flag("HAL_VERDICT_GATE_LINT_ENFORCE")` read and the `StepResult(... E_VERDICT_GATE_LINT ...)`
   return are deleted. The stale `34E0B77B flip-by:2026-08-01` text is removed and the comment says the
   check is a permanent warning and why. No `.py` under `engine_py/bytedigger_engine` may contain
   `E_VERDICT_GATE_LINT`, `HAL_VERDICT_GATE_LINT_ENFORCE`, or a `flip-by:2026-08-01` token on a line that also names `34E0B77B` or `VERDICT_GATE` (the unrelated `flip-by:2026-08-01` at the RED scope allowlist, refs #569, is not this lot's and stays).
2. `error_codes.py`: remove `E_VERDICT_GATE_LINT`; remove the same line from `engine_py/ERROR_CODES.md`
   and `engine_py/bytedigger_engine/ERROR_CODES.md`.
3. `flags_catalog.py`: delete the `HAL_VERDICT_GATE_LINT_ENFORCE` entry. Update the
   `HAL_VERDICT_GATE_LINT` description: the lint only warns, never stops; `=0` disables it at the
   phase 5 acceptance seam and in the hook. The new text must not contain the strings above.
4. `scripts/flip_horizon_ledger.json`: delete the `HAL_VERDICT_GATE_LINT_ENFORCE` entry (12 -> 11).
5. `CHANGELOG.md`: one line under Unreleased/Changed, English, no internal ids beyond `bd#296`.
6. Existing tests that name the flag, changed deliberately (listed, nothing else is touched):
   - `tests/test_bd89_p3d_flag_owner_lint.py`: delete the `HAL_VERDICT_GATE_LINT_ENFORCE` line of
     `PINNED_DEFAULTS` (the pin asserts the flag exists with default `"0"`; the flag is gone).
   - `engine_py/tests/test_bd65_window_d_port.py`: delete
     `test_hal_verdict_gate_lint_enforce_flip_by_retargeted` (asserts the entry exists with
     `flip-by:2026-08-01`, the contract being retired) and drop the matching clause of the module
     docstring. The new test file asserts the opposite.
   - Ledger entry count: no test pins it (checked: `test_bd_flip_horizon_guard.py` checks per-entry
     shape and `ledger ⊆ token flags`; `test_bd89_p3d_flag_owner_lint.py` checks catalog entries).
     Count goes 12 -> 11 and is listed here for the record.

No prompt change. No new switch: `HAL_VERDICT_GATE_LINT=0` stays the only setting; a PR revert is not
a switch and is not offered as one.

Out of scope: the other overdue flags, what the lint checks, the host-side hook, the validator prompt.

## 5. Acceptance criteria

- AC1. `FLAGS` has no `HAL_VERDICT_GATE_LINT_ENFORCE`; `HAL_VERDICT_GATE_LINT` is still `kind=gate`,
  default `"1"`, and its description does not contain the strings of §4.1.
- AC2 (real step `_gate_on_validation` with a run context, `VERDICT: APPROVED` doc without anchor,
  no `HAL_VERDICT_GATE_LINT_ENFORCE`): `status == "ok"`, `error_code is None`, the event log has exactly
  one `verdict_gate_lint_warn` whose `result == "UNANCHORED"`, `rc == 1`, `doc` is the doc path,
  `phase == 5`, `cycle` equals the input cycle. (Fence: passes before the change.)
- AC3. Same input with `HAL_VERDICT_GATE_LINT_ENFORCE=1` in the environment: identical to AC2. (RED.)
- AC4. Same input with `HAL_VERDICT_GATE_LINT=0`: `status == "ok"`, no `verdict_gate_lint_warn` event.
- AC5. A doc the lint agrees with (anchored spec and RED files whose sha256 match, checklist table
  with no FAIL row, matching AC ids): `status == "ok"`, no `verdict_gate_lint_warn` event.
- AC6. `run_gate` raising an exception: `status == "ok"` and one `verdict_gate_lint_warn` with
  `result == "DRIVER_ERROR"` and `rc == 2`, once with `HAL_VERDICT_GATE_LINT_ENFORCE` unset
  (fence) and once with it set (RED).
- AC7. `ERROR_CODES` has no `E_VERDICT_GATE_LINT`; neither `ERROR_CODES.md` mentions it;
  `error_codes.check(root)` reports no unregistered and no dead codes.
- AC8. The ledger has no `HAL_VERDICT_GATE_LINT_ENFORCE` key and `python3 scripts/flip_horizon.py
  --check --today 2026-11-04` does not name it; the ledger has 11 entries and its `until` dates are
  all unchanged from base (no extension).
- AC9. None of the strings of §4.1 (as scoped there) appears in any `.py` under `engine_py/bytedigger_engine`.
- AC10. `HAL_VERDICT_GATE_LINT_ENFORCE` is not read anywhere in `engine_py/bytedigger_engine`
  (no `flag(`/`gate_enabled(`/`os.environ` access with that name; same scan as AC9 covers the name).

## 6. Test plan

One new file `engine_py/tests/test_bd296_verdict_gate_lint_permanent_warn.py` (AC1-AC10); AC2-AC6
go through the real step with a recording event log; only `run_gate` is replaced in AC6, to force the
driver-error branch. The two existing-test edits of §4.6 are the only other test changes. Local runs
are targeted; the full suite is the "full run" stage.
