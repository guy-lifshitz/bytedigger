# bd#218 step 1 — coordinator acceptance (gate cap)

Opus gate cap for Tier 3 is two rounds. Round 1 (`2026-10-03-bd218-s1-gate-r1.md`) and round 2
(`2026-10-03-bd218-s1-gate-r2.md`) both returned REJECTED. The coordinator (MGR, 2026-10-03) waived
round 3 and accepted GREEN on these grounds: the one MAJOR of round 2 is removed by reverting the L16
edit, and the MINOR items are applied. Conditions set by the coordinator: this file in the PR, `git diff`
shows L16 untouched, one Sonnet review of the GREEN diff, CI full suite green.

## Round 2 findings and how each is closed

| gate-r2 item | closed by |
|---|---|
| MAJOR-A: L16 edit contradicts ambient-skip / AC14 | L16 not edited (`git diff origin/main -- engine_py/tests/test_bd141_check_ladder.py` is empty); AC10 restated as "siblings pass unchanged, no existing test file edited"; AC14 pins that the L16-shaped harness gets no `extra_data["preflight"]` |
| MINOR-1: op2b premise wrong for `_verify_validation_citations` | spec r4 op2b: only `_write_validation_doc` changes; AC12 pins the whole chain |
| MINOR-2: binding of `receipt_rung` | spec r4 op2 and AC4/AC5/AC14: phase 5 calls `preflight.receipt_rung` (module attribute) |
| MINOR-3: spec post-conditions without an assertion | AC17 (error record, config-error no record, `informative`, `findings_heads`, sub-directory `git_cwd`) |
| MINOR-4: relative explicit source, sub-directory `git_cwd` | spec r4 op1/op2; AC14 relative case; AC17 sub-directory case |
| MINOR-5: tier line understated | spec header says Tier 3 and lists the four files |
| MINOR-6: reading rule is prose only | `ladder_table` groups carry `informative` (true only for `fresh`/`red`); AC9c, AC15, AC17 |
| MINOR-7: stale AC labels in the RED docstring | docstring now reads AC1–AC17 |
| MINOR-8: `findings_heads` undefined | spec r4: flatten in input order, de-duplicate, first 3; AC17 |

## Evidence

- RED r3: 35 tests, all red at commit before GREEN. GREEN: 35 passed.
- Sibling files of spec §4: 290 passed, 1 skipped, unchanged.
- `git diff --numstat origin/main -- engine_py/bytedigger_engine`: 0 deleted lines in every file.
- Project mypy config on `preflight.py`: clean.
- Not claimed: any cost saving. Known limit (spec §2 op3): no producer of a fresh receipt exists yet, so
  the shadow table is mostly `stale`/`missing` until step s2.
