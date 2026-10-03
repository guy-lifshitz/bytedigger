# bd#89 P3c gate, round 7 (delta audit: commit 5f86ff8)

Base: r6 APPROVED (`docs/decisions/2026-10-03-bd89-p3c-gate-r6.md`). This gate looks only at the delta.
Delta (as the orchestrator reported it; I did not run git): commit `5f86ff8` changes only `engine_py/tests/test_bd89_p2a_phases_1_4_dropped.py`. It renames the bd141 mirror-pin test to `test_ac14_bd141_pins_are_11_drivers_and_13_dispatches` and sets its expected values to `[11]` / `[13]`.
I read the pin (`test_bd89_p2a_phases_1_4_dropped.py:51`, `:904-917`), its target (`test_bd141_p4d_role_template_injections.py:1-40`, `:387-400`, `:558-585`), the spec section 5 sibling list (`2026-10-03-bd89-p3c-deterministic-synthesize-report.md:115-118`), the count history in the P2a and P2b specs, and the `invoke_llm_subprocess(` sites under `engine_py/bytedigger_engine/workflows/`. I did not run tests or git. This file is the only file I wrote.

## Step 1: spec-internal consistency

- **Name and docstring agree.** The new test name and its docstring map entry (`:51`, `test_ac14_bd141_pins_are_11_drivers_and_13_dispatches`) match. No other reference to the old name exists anywhere in the repo: a grep for `12_drivers`, `14_dispatches`, `12 drivers` and `14 dispatch` finds nothing.
- **Count history.** Each step follows from the one before:

  | slice | `len(_DRIVERS)` | direct dispatches (`len(rows)`) |
  |---|---|---|
  | P2b | 17 -> 15 | 21 -> 19 |
  | P2a | 15 -> 12 | 19 -> 15 |
  | bd#91 | unchanged | 15 -> 14 (docstring `:581`) |
  | P3c | 12 -> 11 (spec section 5 item 3 drops the phase-7 entry from bd141 `:387-403`) | 14 -> 13 (docstring `:582`) |

- **Spec text.** Spec section 5 item 3 says "Expected counts in those tables drop by one; no assertion about other sites may change". The new pin values are exactly that.
- **One gap in the spec.** Section 5 item 4 lists `test_bd89_p2a_phases_1_4_dropped.py` as "audit only" (`:119`, the name list). It does not name the AC14 mirror pin at `:914-917`, which item 3 makes stale. This is bookkeeping only (MINOR F1).

## Step 1.5: rule-overlap simulation

Not applicable. The delta touches no dispatcher or classifier. The pin's AST scan, `_compare_constants`, has one branch: it collects every int constant compared against the exact source text `len(_DRIVERS)` / `len(rows)`. The target file has exactly one such compare for each, at `:400` and `:585`, so the scan returns `[11]` and `[13]`.

## Step 2: section 2 vs section 3 cross-check

- **op7 / AC14 trace.** The delta follows section 5 item 3, which drops the phase-7 driver and dispatch. The bd141 target was already changed by RED `98ad094`, and the mirror now agrees with it. No AC was added or removed.
- **Production matches the pins.**
  - **Dispatch count.** A text grep finds 13 `invoke_llm_subprocess(` occurrences across 5 workflow files, and `phase_7_synthesize.py` has none. 13 matches `len(rows) == 13`. The grep counts text, not AST call nodes, but the total agrees.
  - **Driver count.** The `_DRIVERS` dict at `:387-399` holds 11 keys and has no phase-7 key. 11 matches `len(_DRIVERS) == 11`.

## Step 3: RED adequacy (is the assertion weakened?)

The pin is not weakened.
- **Same shape.** The assertion is still an exact list equality (`== [11]`, `== [13]`), the same shape as before.
- **Same failures caught.** It still fails if:
  - the bd141 assert is deleted (the scan returns `[]`);
  - a second competing compare is added (for example `[11, 12]`);
  - the bd141 count drifts.
- **Only the constants changed**, and only by the -1 that section 5 prescribes. No tolerance, range or `>=` was added, and nothing else in the module was changed.
- **GUARD status unchanged.** It is still "green by construction". It mirrors a sibling file, as P2a AC14 designed, and does not mock the unit under test.
- **The test is not vacuous.** The target file's `:400` is a module-level assert, so a wrong count breaks collection of bd141 itself. The mirror pin additionally catches the assert being removed or loosened.

## Step 4: reachability (section 1y)

- **Point:** the module-level `assert len(_DRIVERS) == 11` (`test_bd141...:400`) and `assert len(rows) == 13` in `test_ac5_dispatch_call_count_is_pinned` (`:585`).
- **Host:** `_scan_dispatches()` (`:558-568`) walks `_workflows_dir().glob("*.py")` in the production tree.
- **Test path:** `test_ac14_bd141_pins_are_11_drivers_and_13_dispatches` reads those constants with the AST scan.

The chain is intact.

## Other stale count pins

- **Cross-file mirrors.** `test_bd89_p2a...:915` is the only test that reads another test file's count constants. A grep for references to `test_bd141_p4d`, `test_bd150_class_i`, `test_bd119_role_template.py` and `test_llm_subprocess_allowed_tools.py` finds only `:915` as a pin. The other hits are comments.
- **Sibling tables from section 5 item 3.** None of bd150, bd119, gh705, F9F7E4FD, llm_subprocess_allowed_tools, timeout_policy or gh1626c holds an `assert len(...) == N` site/registry count. All their `len` asserts are per-call counts (1, 2, 3) that P3c does not affect.
- **`test_ac14_no_other_registry_count_of_14`** looks only at registry-shaped expressions. It does not match `len(rows)` and is unaffected.
- **Stale prose, not asserts (MINOR F2).** The bd141 module docstring is out of date, which is pre-existing drift and not part of this delta:
  - `:19` and `:21` say `<15 producers>`;
  - `:29` says "the 20th dispatch ... the 19 direct calls";
  - `:6` says "the other 16 builders".

## Adversarial edges (not covered by any section 3 AC)

1. **A future sibling that edits only one side.** If a later slice changes the bd141 count but not this mirror, or the reverse, the two disagree and one test fails. That is the intended tripwire. No new edge comes from this delta.

Other edges: none.

## Findings

1. MINOR F1: spec section 5 item 4 (`:118`) lists `test_bd89_p2a_phases_1_4_dropped.py` as "audit only". It misses that its AC14 mirror pin (`:914-917`) must follow item 3's -1. The delta does the right thing. A one-line note in the spec or the PR body would record it.
2. MINOR F2: the bd141 module docstring (`:6`, `:19`, `:21`, `:29`) has stale counts: 16 builders, 15 producers, 20th/19 dispatches. The asserts are correct (11 / 13). This is prose only, accumulated before this slice; an optional doc fixup.

There are no MAJOR findings. The pin change is correct, follows spec section 5 item 3, matches production (13 direct dispatches, 11 drivers), and keeps the assertion as strict as it was. I found no other stale count pins.

VERDICT: APPROVED
