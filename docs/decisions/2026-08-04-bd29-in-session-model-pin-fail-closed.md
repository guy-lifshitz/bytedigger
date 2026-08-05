# bd#29 — in-session model-pin fail-closed flip (supersede 220E5F63)

**Class:** declaration without measurement (gate B-3 of bd#10). The path did not reach the chokepoint
not because the chokepoint failed to cover it, but because it **did not fill the channel the
chokepoint reads**. The shield was green the whole time the hole stayed open.
An accompanying collision: agreement 220E5F63/GH#222 ("warn-only") and CL:99
("a declared pin MUST fail on mismatch") cannot both be true at once.

**Chokepoint:** `llm_subprocess._pin_mismatch_refusal` (`:1088`, called from
`invoke_llm_subprocess` at `:1301`). It **already stands on the path of EVERY backend** — after
the dispatch, before the return. The channel it reads is `StepResult.data["observed_model"]`.

**The key measurement:** `_invoke_in_session` assembles `data` (`:939-951`) from
`raw_response`, `response_bytes`, `model`, `tokens_out`, `tokens_in`, `cost_usd`,
`worker_written_paths`, `manifest_source` — **`observed_model` is not there**. So
`_pin_mismatch_refusal` on an in-session result gets `observed = None` and comes out
`not-checked`, while the parallel warn-only branch (`:929-932`,
`_detect_nonhardgate_model_drift`) emits a warning and lets the step through.

⇒ **The flip does not require a second implementation of the check.** It falls out as a CONSEQUENCE
of the chokepoint, exactly as the issue's item 2 demands: it is enough to fill the channel and remove
the superseded warn-only call.

**§1b live base**, taken BEFORE the freeze, `ada6585`, from `engine_py`, after wiping
`build`/`__pycache__`: **`4497 passed / 39 skipped / 0 failed`**, 297 s.

---

## §1. Exposure measurement (the issue's entry requirement, item 1) — by number, on this host

An AST scan of every production call site of `invoke_llm_subprocess` in `bytedigger_engine/`:

| | sites |
|---|---|
| total production sites | **22** |
| `hard_gate=True` (already fail-closed via `_assert_in_session_model_or_downgrade`) | **7** |
| **non-hard-gate — this is precisely the warn-only exposure** | **15** |
| sites not passing a model pin | **0** |

**Against myself — the first count was wrong, and I caught it by an impossible signal.**
The first scan gave "2 sites without a pin" (`phase_45_spec.py:3854,3905`), even though `model: str` is a
**mandatory** parameter with no default. The impossibility was the evidence: both
sites call `invoke_llm_subprocess(**invoke_kwargs)`, and the dict is built above and carries
`model=rev_model` **and** `hard_gate=True`. So the static scan erred TWICE — on the
pin and on `hard_gate`; those two sites are not "non-hard-gate without a pin" but ordinary hard-gate ones.
The numbers above are already corrected. **The form: a splat call is opaque to a static scan;
check kwarg-based classification against impossible combinations.**

**Runtime exposure.** `_DEFAULT_BACKEND = "agent-sdk"`; in-session is enabled only by an
explicit `backend=` or `HAL_RUNNER_BACKEND=claude-in-session` (`_resolve_backend`,
kwarg > env > default). **No production site selects in-session** — the single
textual match (`phase_6_review.py:973`) merely READS the resolved backend in order to
decide about `straggler_abort`.

⇒ **What happens under fail-closed:** the 15 non-hard-gate sites gain the ability to fail hard,
but only when three conditions coincide — the run is in-session (explicit opt-in),
the servicer returned a `dispatched_model`, and its family differs from the pin. No
existing caller breaks for lack of a pin, because there are **zero** such callers.
That makes the flip cheap in risk — and it is exactly this measurement that was missing in bd#10.

## §2. Decisions

**D1 — fill the channel, do not write a second check.** `_invoke_in_session` puts into
`data` the key `observed_model` with the value `result_obj.get("dispatched_model")`.
Adjudication stays exactly one — the chokepoint's.

**D2 — remove the superseded warn-only call** `_detect_nonhardgate_model_drift` from
`_invoke_in_session` (`:929-932`). **Do NOT delete the function itself** — its unit tests
AC5–AC9 remain in force (the issue's item 5), and it retains value as a
pure predicate.

**D3 — the event is preserved, the issue's item 4.** The chokepoint emits
`model_pin_mismatch` with `observed_model`, `pinned_model`, `step_name` (+ `phase`,
`pinned_family`, `observed_family`, `severity="error"`, `chokepoint=True`). The surviving
halves of AC10 are re-anchored in a **new** oracle (AC2 below) rather than asserted only by the
file the lot itself edits.

**D4 — "unrecognised family" is NOT touched.** bd#10 decided explicitly: "an adapter that
reported nothing, or reported an unrecognised token, is `not-checked` — an unrecognised
token is not evidence of drift". Making that fail-closed would mean **overturning the bd#10
decision**, not carrying out bd#29. The price is named: a servicer that returned an unknown model
token will not raise the gate. If that needs changing, it is a separate subject with its own
argument.

**D5 — 220E5F63 is superseded.** Date 2026-08-04, reason: conflict with CL:99, which
requires a failure on mismatch of a declared pin. Warn-only was legitimate until the
level declared otherwise; after CL:99 the two rules cannot both be true at
once. The label `attest.REQUIREMENT_LABELS["R3.3"]` changes from `"in-session-warn-only"`
to `"chokepoint-enforced"`.

## §3. §5 Scope

**Edited**
- `engine_py/bytedigger_engine/llm_subprocess.py` — D1 (+1 key), D2 (removal of the call).
- `engine_py/bytedigger_engine/conformance/attest.py` — the R3.3 label (D5).
- `engine_py/tests/test_2FDA949D_model_pin_warn.py` — **only** the expected status and error
  code in AC10 (the issue's item 5). The AC5–AC9 unit asserts are not touched.
- `engine_py/tests/test_bd10_l3_authorship.py` — the expected value of the R3.3 label.

**New files**
- `engine_py/tests/test_bd29_in_session_pin_fail_closed.py` — RED.
- this document (supersession of 220E5F63, the issue's item 6).

**§1v — NOT in scope**
- `_detect_nonhardgate_model_drift` as a function (D2), its unit tests.
- `_assert_in_session_model_or_downgrade` — the hard-gate path is already fail-closed.
- `_pin_mismatch_refusal` — not changed by a single line; that is the whole point (the flip as a
  consequence of the chokepoint, not a second implementation of it).
- "Unrecognised family" (D4).

## §4. §1a Sibling audit

| file | tests | what it reads |
|---|---|---|
| `test_bd10_l3_authorship.py` | 29 | `REQUIREMENT_LABELS`, `observed_model`, `model_pin_mismatch` — **direct risk** (pins `"in-session-warn-only"` at `:200`) |
| `test_2FDA949D_model_pin_warn.py` | 11 | AC10 end-to-end + the AC5–AC9 units — **the subject of the edit** |
| `test_02FF48F4_model_pin_insession.py` | 8 | `observed_model`, hard-gate downgrade |
| `test_llm_subprocess_hard_gate.py` | 8 | `observed_model` |

**56** tests in total, to be run with `--require-clean`.

## §5. Acceptance criteria

Every leg runs `_invoke_in_session` end-to-end through the file protocol (the AC10 form,
§1y), the UUT is not mocked.

- **AC1 (the subject).** A non-hard-gate step whose `.res.json` carries a `dispatched_model` of a
  family different from the pin ⇒ `status == "error"`, `error_code == "E_MODEL_PIN_MISMATCH"`,
  `recoverable is False`.
- **AC2 (re-anchoring the surviving halves of AC10, item 4).** The same run still emits
  `model_pin_mismatch`, and the event carries `observed_model`, `pinned_model`, `step_name`.
  Asserted in a NEW file, not only in the one the lot edits.
- **AC3 (NEGATIVE LEG — the channel).** The `StepResult.data` of an in-session result carries
  `observed_model` equal to `dispatched_model`. Without this AC the "flip" could have been done
  by a second check inside `_invoke_in_session` while the channel stayed empty — that is,
  precisely defect B-3, reproduced in the guise of a fix.
- **AC4 (NEGATIVE LEG — the gate is not inert).** A fail-closed that never fails
  does not exist: a run WITHOUT drift (matching families) must give
  `status == "ok"` and NOT A SINGLE `model_pin_mismatch`. Without this leg a "fix"
  that fails everything indiscriminately would be green on AC1.
- **AC5 (the bd#10 boundary, D4).** `dispatched_model` absent from the `.res.json` ⇒
  `status == "ok"`, no events (`not-checked`). Pins that bd#29 did NOT overturn the
  bd#10 decision about the unrecognised/absent case.
- **AC6 (hard-gate untouched).** `hard_gate=True` with drift is still served by
  `_assert_in_session_model_or_downgrade` and fails with its own code rather than being replaced
  by the chokepoint's.
- **AC7 (D5, the label).** `attest.REQUIREMENT_LABELS["R3.3"] == "chokepoint-enforced"`.

## §5a. FOUND ALONG THE WAY, NOT FIXED HERE: two guards on the hard-gate path, the later overwrites the earlier

Measured while writing AC6. `_assert_in_session_model_or_downgrade` refuses with its own
code **`E_HARD_GATE_MODEL_DOWNGRADE`** and puts the key `observed_model` into `data`
(`:3386`). Further on, `invoke_llm_subprocess` at `:1301` calls `_pin_mismatch_refusal`,
which reads **that same** `observed_model`, sees the family difference and **overwrites
the result with its own `E_MODEL_PIN_MISMATCH`**.

⇒ The hard-gate guard's code **does not reach the caller** when the families additionally
differ. Measured: `hard_gate=True`, pin `sonnet`, dispatched `haiku` ⇒ the output is
`E_MODEL_PIN_MISMATCH`, not `E_HARD_GATE_MODEL_DOWNGRADE`.

The defect is **pre-existing**; bd#29 does not create it and does **not fix** it here — that is a second
subject with its own argument (which of the two codes is right, and whether the later guard
should respect a refusal already taken). AC6 pins today's observable behaviour so that the flip
does not shift it unnoticed; when the defect is fixed, AC6 must fail and demand a
decision rather than stay silent.

## §5b. DEPARTURE FROM THE ISSUE'S ITEM 5 — with a measurement, not out of convenience

The issue requires: "The edit to `test_2FDA949D_model_pin_warn.py` is limited to the expected status
and error code in AC10." **Measured: this is unattainable, because AC10's subject moved
one level up.**

AC10 calls `_invoke_in_session` DIRECTLY. After D1/D2 the internal drift function
decides nothing any more: adjudication is `_pin_mismatch_refusal`, called from
`invoke_llm_subprocess` (`:1301`). The direct call now yields `status="ok"` and **zero**
events (measured: `events captured: ['resolver_runner_request_dir_resolved',
'runner_request_built', 'runner_result_consumed']`). That is, neither a status nor an error
code onto which AC10 could be "re-targeted" exists at that level.

**What was done instead:** AC10 was repurposed as a regression guard OF THE REMOVAL ITSELF —
the internal function must no longer emit or block. The surviving halves of the
old contract (the event with `observed_model`/`pinned_model`/`step_name` and the step failure)
are re-anchored in the NEW oracle `test_bd29_in_session_pin_fail_closed.py`
(AC2/AC1) — exactly as the issue's item 4 requires, so that the change is not judged by an artefact
the lot edits itself. The AC5–AC9 unit asserts are untouched.

## §6. What this PR does NOT claim

- It does not claim the in-session path is protected against an unrecognised model token (D4).
- It does not claim the warn-only form has disappeared from the code: the predicate function remains,
  only its call from the production path was removed.
- It does not change the hard-gate path and does not touch `_pin_mismatch_refusal`.
